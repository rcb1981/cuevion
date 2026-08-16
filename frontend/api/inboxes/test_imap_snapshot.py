from __future__ import annotations

import json
import re
import sys
import unittest
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import imap_connect_preview
from api.inboxes import imap_snapshot


RAW_MESSAGE = (
    b"From: Sender <sender@example.com>\r\n"
    b"To: Owner <owner@example.com>\r\n"
    b"Date: Tue, 01 Jul 2025 10:00:00 +0000\r\n"
    b"Message-ID: <Local-Part@EXAMPLE.COM>\r\n"
    b"Subject: Provider snapshot\r\n"
    b"\r\n"
    b"Snapshot body.\r\n"
)


class RecordingMailbox:
    def __init__(
        self,
        *,
        uid_validity="456",
        uid_validity_responses=None,
        uid_search="123",
        select_response=("OK", [b"1"]),
        fetch_response=None,
    ):
        self.uid_validity = uid_validity
        self.uid_validity_responses = (
            None
            if uid_validity_responses is None
            else list(uid_validity_responses)
        )
        self.uid_validity_response_index = 0
        self.uid_search = uid_search
        self.select_response = select_response
        self.fetch_response = fetch_response
        self.select_calls: list[str] = []
        self.select_readonly_calls: list[tuple[str, bool]] = []
        self.response_calls: list[str] = []
        self.uid_calls: list[tuple] = []
        self.unsafe_calls: list[tuple] = []
        self.operations: list[tuple] = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def select(self, folder, readonly=False):
        self.select_calls.append(folder)
        self.select_readonly_calls.append((folder, readonly))
        self.operations.append(("select", folder))
        return self._resolve(self.select_response)

    def response(self, name):
        self.response_calls.append(name)
        self.operations.append(("response", name))
        if self.uid_validity_responses is None:
            value = self.uid_validity
        else:
            if self.uid_validity_response_index >= len(
                self.uid_validity_responses
            ):
                raise AssertionError("unexpected UIDVALIDITY read")
            value = self.uid_validity_responses[
                self.uid_validity_response_index
            ]
            self.uid_validity_response_index += 1
        value = self._resolve(value)
        if type(value) in (list, tuple):
            return value
        return "UIDVALIDITY", [str(value).encode("ascii")]

    def uid(self, command, *arguments):
        call = (command, *arguments)
        self.uid_calls.append(call)
        self.operations.append(("uid", *call))
        if command == "SEARCH":
            value = self._resolve(self.uid_search)
            if type(value) in (list, tuple):
                return value
            if type(value) is str:
                value = value.encode("ascii")
            return "OK", [value]
        if command == "FETCH":
            if self.fetch_response is not None:
                return self._resolve(self.fetch_response)
            uid = arguments[0]
            metadata = (
                f"1 (UID {uid} BODY[] {{{len(RAW_MESSAGE)}}}".encode("ascii")
            )
            return "OK", [(metadata, RAW_MESSAGE), b")"]
        raise AssertionError(f"unexpected UID command: {command}")

    def search(self, *arguments):
        self.unsafe_calls.append(("sequence_search", *arguments))
        raise AssertionError("identity reads must not SEARCH")

    def copy(self, *arguments):
        self.unsafe_calls.append(("copy", *arguments))
        raise AssertionError("COPY must not be used")

    def store(self, *arguments):
        self.unsafe_calls.append(("store", *arguments))
        raise AssertionError("STORE must not be used")

    def expunge(self, *arguments):
        self.unsafe_calls.append(("expunge", *arguments))
        raise AssertionError("EXPUNGE must not be used")


def fetched_message(uid: str, *, message_id: str | None = None):
    raw = RAW_MESSAGE
    if message_id is not None:
        raw = RAW_MESSAGE.replace(
            b"<Local-Part@EXAMPLE.COM>",
            f"<{message_id}>".encode("ascii"),
        )
    return message_from_bytes(raw), True, uid, False


def preview_for(_message, index, _email, unread, uid, flagged=False):
    return {
        "id": f"preview-{index}",
        "imapUid": uid,
        "unread": unread,
        "flagged": flagged,
    }


def uid_fetch_response(raw_message: bytes, uid: str = "123"):
    metadata = f"1 (UID {uid} BODY[] {{{len(raw_message)}}}".encode("ascii")
    return "OK", [(metadata, raw_message), b")"]


class ImapMessageIdentityTests(unittest.TestCase):
    def test_reads_exact_uid_after_safely_quoted_select(self):
        mailbox = RecordingMailbox()
        result = imap_snapshot.read_imap_message_identity(
            mailbox,
            folder='Source "A"\\2024',
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.select_calls,
            [r'"Source \"A\"\\2024"'],
        )
        self.assertEqual(mailbox.response_calls, ["UIDVALIDITY"])
        self.assertEqual(
            mailbox.uid_calls,
            [("FETCH", "123", "(UID BODY.PEEK[])")],
        )
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertEqual(
            result["identity"]["providerFolder"],
            'Source "A"\\2024',
        )
        self.assertEqual(result["identity"]["imapUid"], "123")
        self.assertEqual(result["identity"]["uidValidity"], "456")
        self.assertEqual(
            result["identity"]["rfcMessageId"],
            "Local-Part@example.com",
        )
        self.assertRegex(result["identity"]["fingerprint"], r"\A[0-9a-f]{64}\Z")

    def test_fingerprint_is_deterministic_and_internal(self):
        first = imap_snapshot.read_imap_message_identity(
            RecordingMailbox(),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        second = imap_snapshot.read_imap_message_identity(
            RecordingMailbox(),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        self.assertEqual(
            first["identity"]["fingerprint"],
            second["identity"]["fingerprint"],
        )
        self.assertNotIn("Snapshot body", json.dumps(first))

    def test_uidvalidity_mismatch_stops_before_uid_fetch(self):
        mailbox = RecordingMailbox(uid_validity="457")
        result = imap_snapshot.read_imap_message_identity(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "uid_validity_changed")
        self.assertEqual(mailbox.uid_calls, [])

    def test_invalid_inputs_stop_before_provider_calls(self):
        cases = (
            {"folder": ' INBOX', "uid": "123", "expected_uid_validity": "456"},
            {"folder": "INBOX", "uid": "01", "expected_uid_validity": "456"},
            {"folder": "INBOX", "uid": "1:2", "expected_uid_validity": "456"},
            {"folder": "INBOX", "uid": "123", "expected_uid_validity": "0456"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                mailbox = RecordingMailbox()
                result = imap_snapshot.read_imap_message_identity(
                    mailbox,
                    **arguments,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(mailbox.select_calls, [])
                self.assertEqual(mailbox.uid_calls, [])

    def test_uid_fetch_response_must_be_bounded_exact_and_complete(self):
        malformed_responses = (
            ("NO", [b"private provider detail"]),
            ("OK", []),
            ("OK", [(b"1 (UID 124 BODY[] {1}", b"x"), b")"]),
            ("OK", [(b"1 (UID 123 FLAGS () BODY[] {1}", b"x"), b")"]),
            ("OK", [(b"1 (UID 123 BODY[] {2}", b"x"), b")"]),
            ("OK", [(b"1 (UID 123 BODY[] {1}", b"x"), b"]"]),
            (
                "OK",
                [
                    (b"1 (UID 123 BODY[] {1}", b"x"),
                    b")",
                    b"extra",
                ],
            ),
            ("OK", [(b"1 (UID 123 BODY[] {26214401}", b""), b")"]),
        )
        for fetch_response in malformed_responses:
            with self.subTest(fetch_response=repr(fetch_response)[:120]):
                mailbox = RecordingMailbox(fetch_response=fetch_response)
                result = imap_snapshot.read_imap_message_identity(
                    mailbox,
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "message_identity_unconfirmed",
                )
                serialized = json.dumps(result)
                self.assertNotIn("private provider detail", serialized)

    def test_canonical_absent_uid_fetch_has_specific_safe_error(self):
        mailbox = RecordingMailbox(fetch_response=("OK", [None]))
        result = imap_snapshot.read_imap_message_identity(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "message_not_found")
        self.assertEqual(result["error"]["stage"], "message_fetch")


class ImapReplySourceTests(unittest.TestCase):
    def test_reads_only_exact_uid_body_peek_from_readonly_quoted_folder(self):
        raw_message = (
            b"Message-ID: <Source-Local@EXAMPLE.COM>\r\n"
            b"References: <First@EXAMPLE.COM> <second@example.net>\r\n"
            b"In-Reply-To: <Parent@EXAMPLE.COM>\r\n"
            b"Subject: Reply source\r\n"
            b"\r\n"
            b"Source body.\r\n"
        )
        mailbox = RecordingMailbox(
            fetch_response=uid_fetch_response(raw_message),
        )

        result = imap_snapshot.read_imap_reply_source(
            mailbox,
            folder='Source "A"\\2024',
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            mailbox.select_readonly_calls,
            [(r'"Source \"A\"\\2024"', True)],
        )
        self.assertEqual(mailbox.response_calls, ["UIDVALIDITY"])
        self.assertEqual(
            mailbox.uid_calls,
            [("FETCH", "123", "(UID BODY.PEEK[])")],
        )
        self.assertEqual(mailbox.unsafe_calls, [])
        self.assertEqual(
            mailbox.operations,
            [
                ("select", r'"Source \"A\"\\2024"'),
                ("response", "UIDVALIDITY"),
                ("uid", "FETCH", "123", "(UID BODY.PEEK[])"),
            ],
        )
        self.assertEqual(
            result["source"],
            {
                "providerFolder": 'Source "A"\\2024',
                "imapUid": "123",
                "uidValidity": "456",
                "messageId": "<Source-Local@example.com>",
                "references": [
                    "<First@example.com>",
                    "<second@example.net>",
                ],
                "inReplyTo": "<Parent@example.com>",
            },
        )
        self.assertNotIn("Source body", json.dumps(result))

    def test_uidvalidity_mismatch_stops_before_exact_uid_fetch(self):
        mailbox = RecordingMailbox(uid_validity="457")
        result = imap_snapshot.read_imap_reply_source(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "uid_validity_changed")
        self.assertEqual(result["source"], None)
        self.assertEqual(mailbox.uid_calls, [])

    def test_noncanonical_selected_uidvalidity_is_unavailable(self):
        mailbox = RecordingMailbox(uid_validity="0456")
        result = imap_snapshot.read_imap_reply_source(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "uid_validity_unavailable",
        )
        self.assertEqual(mailbox.uid_calls, [])

    def test_invalid_inputs_stop_before_any_provider_call(self):
        cases = (
            {
                "folder": " INBOX",
                "uid": "123",
                "expected_uid_validity": "456",
                "code": "invalid_folder",
            },
            {
                "folder": "INBOX",
                "uid": "01",
                "expected_uid_validity": "456",
                "code": "invalid_imap_uid",
            },
            {
                "folder": "INBOX",
                "uid": "1:2",
                "expected_uid_validity": "456",
                "code": "invalid_imap_uid",
            },
            {
                "folder": "INBOX",
                "uid": "123",
                "expected_uid_validity": "0456",
                "code": "invalid_uid_validity",
            },
        )
        for case in cases:
            with self.subTest(case=case):
                mailbox = RecordingMailbox()
                result = imap_snapshot.read_imap_reply_source(
                    mailbox,
                    folder=case["folder"],
                    uid=case["uid"],
                    expected_uid_validity=case["expected_uid_validity"],
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], case["code"])
                self.assertEqual(mailbox.operations, [])

    def test_select_failures_are_safe_and_stop_before_uidvalidity(self):
        failures = (
            (
                RuntimeError("private provider selection detail"),
                "provider_unavailable",
            ),
            (("NO", [b"private provider selection detail"]), "folder_unavailable"),
            (("OK",), "folder_unavailable"),
        )
        for select_response, expected_code in failures:
            with self.subTest(select_response=select_response):
                mailbox = RecordingMailbox(select_response=select_response)
                result = imap_snapshot.read_imap_reply_source(
                    mailbox,
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    expected_code,
                )
                self.assertEqual(mailbox.response_calls, [])
                self.assertEqual(mailbox.uid_calls, [])
                self.assertNotIn(
                    "private provider selection detail",
                    json.dumps(result),
                )

    def test_fetch_failures_are_safe_and_identity_unconfirmed(self):
        secret_body = b"Message-ID: <secret@example.com>\r\n\r\nSECRET BODY"
        failures = (
            RuntimeError("private provider fetch detail"),
            ("NO", [b"private provider fetch detail"]),
            uid_fetch_response(secret_body, uid="124"),
            (
                "OK",
                [
                    (
                        b"1 (UID 123 FLAGS () BODY[] {1}",
                        b"x",
                    ),
                    b")",
                ],
            ),
        )
        for fetch_response in failures:
            with self.subTest(fetch_response=repr(fetch_response)[:100]):
                mailbox = RecordingMailbox(fetch_response=fetch_response)
                result = imap_snapshot.read_imap_reply_source(
                    mailbox,
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "message_identity_unconfirmed",
                )
                serialized = json.dumps(result)
                self.assertNotIn("private provider fetch detail", serialized)
                self.assertNotIn("SECRET BODY", serialized)

    def test_missing_malformed_or_ambiguous_message_id_is_unthreadable(self):
        raw_messages = (
            b"Subject: Missing\r\n\r\nbody",
            b"Message-ID: not a message id\r\n\r\nbody",
            b"Message-ID: <one@example.com> trailing garbage\r\n\r\nbody",
            b"Message-ID: <one@example.com> <two@example.com>\r\n\r\nbody",
            (
                b"Message-ID: <one@example.com>\r\n"
                b"Message-ID: <two@example.com>\r\n\r\nbody"
            ),
            (
                b"Message-ID: <one@example.com>\r\n"
                b"Message-ID: malformed\r\n\r\nbody"
            ),
        )
        for raw_message in raw_messages:
            with self.subTest(raw_message=raw_message):
                mailbox = RecordingMailbox(
                    fetch_response=uid_fetch_response(raw_message),
                )
                result = imap_snapshot.read_imap_reply_source(
                    mailbox,
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["source"], None)
                self.assertEqual(
                    result["error"]["code"],
                    "imap_reply_source_unthreadable",
                )

    def test_identical_duplicate_message_id_headers_are_unthreadable(self):
        raw_message = (
            b"Message-ID: <same@example.com>\r\n"
            b"Message-ID: <same@example.com>\r\n"
            b"\r\n"
            b"body"
        )
        result = imap_snapshot.read_imap_reply_source(
            RecordingMailbox(
                fetch_response=uid_fetch_response(raw_message),
            ),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["source"], None)
        self.assertEqual(
            result["error"]["code"],
            "imap_reply_source_unthreadable",
        )

    def test_folded_bcc_and_cc_token_injection_drops_ancestry(self):
        raw_message = (
            b"Message-ID: <source@EXAMPLE.COM>\r\n"
            b"References: <kept@EXAMPLE.COM>\r\n"
            b"\tBcc: <victim@example.com>\r\n"
            b"In-Reply-To: <parent@EXAMPLE.COM>\r\n"
            b"\tCc: <victim@example.com>\r\n"
            b"\r\n"
            b"body"
        )
        mailbox = RecordingMailbox(
            fetch_response=uid_fetch_response(raw_message),
        )
        result = imap_snapshot.read_imap_reply_source(
            mailbox,
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"]["messageId"], "<source@example.com>")
        self.assertEqual(result["source"]["references"], [])
        self.assertIsNone(result["source"]["inReplyTo"])
        self.assertNotIn("victim@example.com", json.dumps(result))

    def test_comment_hidden_tokens_are_ignored_without_hiding_real_ancestry(self):
        raw_message = (
            b"Message-ID: <source@example.com>\r\n"
            b"References: (Bcc: Victim <victim@example.com>) "
            b"<kept@example.com>\r\n"
            b"In-Reply-To: (Cc: Victim <victim@example.com>) "
            b"<parent@example.com>\r\n"
            b"\r\n"
            b"body"
        )
        result = imap_snapshot.read_imap_reply_source(
            RecordingMailbox(
                fetch_response=uid_fetch_response(raw_message),
            ),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["source"]["references"],
            ["<kept@example.com>"],
        )
        self.assertEqual(
            result["source"]["inReplyTo"],
            "<parent@example.com>",
        )
        self.assertNotIn("victim@example.com", json.dumps(result))

    def test_cfws_quoted_local_and_domain_literal_message_ids_remain_valid(self):
        raw_message_ids = (
            b"(source comment) <source@example.com> (tail comment)",
            b'<"quoted local"@example.com>',
            b"<source@[IPv6:2001:db8::1]>",
        )
        for raw_message_id in raw_message_ids:
            with self.subTest(raw_message_id=raw_message_id):
                raw_message = (
                    b"Message-ID: " + raw_message_id + b"\r\n\r\nbody"
                )
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertTrue(result["ok"])
                self.assertTrue(result["source"]["messageId"].startswith("<"))
                self.assertTrue(result["source"]["messageId"].endswith(">"))

    def test_quoted_greater_than_and_empty_quoted_local_remain_valid(self):
        cases = (
            (
                b'<"a>b"@EXAMPLE.COM>',
                '<"a>b"@example.com>',
            ),
            (
                b'<"a\\>b"@EXAMPLE.COM>',
                '<"a\\>b"@example.com>',
            ),
            (
                b'<""@EXAMPLE.COM>',
                '<""@example.com>',
            ),
            (
                b"<source@[route>segment@host]>",
                "<source@[route>segment@host]>",
            ),
        )
        for raw_message_id, expected_message_id in cases:
            with self.subTest(raw_message_id=raw_message_id):
                raw_message = (
                    b"Message-ID: " + raw_message_id + b"\r\n\r\nbody"
                )
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertTrue(result["ok"])
                self.assertEqual(
                    result["source"]["messageId"],
                    expected_message_id,
                )

    def test_compat_message_id_rejects_malformed_or_appended_content(self):
        raw_messages = (
            b'Message-ID: <"a>b@example.com>\r\n\r\nbody',
            b'Message-ID: <"a>b"@example.com> trailing\r\n\r\nbody',
            (
                b'Message-ID: <"a>b"@example.com>\r\n'
                b"\tBcc: <victim@example.com>\r\n\r\nbody"
            ),
            b'Message-ID: <"a\r\n\t>b"@example.com>\r\n\r\nbody',
            b"Message-ID: <source@[route>segment@host>\r\n\r\nbody",
        )
        for raw_message in raw_messages:
            with self.subTest(raw_message=raw_message):
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "imap_reply_source_unthreadable",
                )
                self.assertNotIn("victim@example.com", json.dumps(result))

    def test_source_rejects_folding_inside_quoted_or_literal_message_id(self):
        raw_messages = (
            (
                b'Message-ID: <"a\r\n'
                b' Bcc: victim@example.net"@example.com>\r\n'
                b"\r\nbody"
            ),
            (
                b"Message-ID: <source@[route\r\n"
                b" segment@host]>\r\n"
                b"\r\nbody"
            ),
        )
        for raw_message in raw_messages:
            with self.subTest(raw_message=raw_message):
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "imap_reply_source_unthreadable",
                )
                self.assertNotIn("victim@example.net", json.dumps(result))

    def test_quoted_and_domain_literal_greater_than_ancestry_is_preserved(self):
        cases = (
            (
                (
                    b"Message-ID: <source@example.com>\r\n"
                    b'References: <"older>one"@EXAMPLE.COM> '
                    b"<ancestor@[route>segment@host]>\r\n"
                    b'In-Reply-To: <"parent>one"@EXAMPLE.COM>\r\n'
                    b"\r\nbody"
                ),
                [
                    '<"older>one"@example.com>',
                    "<ancestor@[route>segment@host]>",
                ],
                '<"parent>one"@example.com>',
            ),
            (
                (
                    b"Message-ID: <source@example.com>\r\n"
                    b"In-Reply-To: <parent@[route>segment@host]>\r\n"
                    b"\r\nbody"
                ),
                [],
                "<parent@[route>segment@host]>",
            ),
        )
        for raw_message, expected_references, expected_in_reply_to in cases:
            with self.subTest(raw_message=raw_message):
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertTrue(result["ok"])
                self.assertEqual(
                    result["source"]["references"],
                    expected_references,
                )
                self.assertEqual(
                    result["source"]["inReplyTo"],
                    expected_in_reply_to,
                )

    def test_ancestry_drops_folding_inside_quoted_or_literal_token(self):
        cases = (
            (
                (
                    b"Message-ID: <source@example.com>\r\n"
                    b'References: <"ancestor\r\n'
                    b' Bcc: victim@example.net"@example.com>\r\n'
                    b"In-Reply-To: <parent@example.com>\r\n"
                    b"\r\nbody"
                ),
                [],
                None,
            ),
            (
                (
                    b"Message-ID: <source@example.com>\r\n"
                    b"In-Reply-To: <parent@[route\r\n"
                    b" segment@host]>\r\n"
                    b"\r\nbody"
                ),
                [],
                None,
            ),
        )
        for raw_message, expected_references, expected_in_reply_to in cases:
            with self.subTest(raw_message=raw_message):
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertTrue(result["ok"])
                self.assertEqual(
                    result["source"]["references"],
                    expected_references,
                )
                self.assertEqual(
                    result["source"]["inReplyTo"],
                    expected_in_reply_to,
                )
                self.assertNotIn("victim@example.net", json.dumps(result))

    def test_present_tainted_references_disable_in_reply_to_fallback(self):
        raw_message = (
            b"Message-ID: <source@example.com>\r\n"
            b'References: <"valid>ancestor"@example.com> '
            b"<invalid..ancestor@example.com>\r\n"
            b"In-Reply-To: <parent@[route>segment@host]>\r\n"
            b"\r\nbody"
        )
        result = imap_snapshot.read_imap_reply_source(
            RecordingMailbox(
                fetch_response=uid_fetch_response(raw_message),
            ),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"]["references"], [])
        self.assertIsNone(result["source"]["inReplyTo"])

    def test_ambiguous_in_reply_to_is_not_exposed_as_fallback_ancestry(self):
        raw_messages = (
            (
                b"Message-ID: <source@example.com>\r\n"
                b"In-Reply-To: <one@example.com> <two@example.com>\r\n"
                b"\r\nbody"
            ),
            (
                b"Message-ID: <source@example.com>\r\n"
                b"In-Reply-To: <one@example.com>\r\n"
                b"In-Reply-To: <one@example.com>\r\n"
                b"\r\nbody"
            ),
        )
        for raw_message in raw_messages:
            with self.subTest(raw_message=raw_message):
                result = imap_snapshot.read_imap_reply_source(
                    RecordingMailbox(
                        fetch_response=uid_fetch_response(raw_message),
                    ),
                    folder="INBOX",
                    uid="123",
                    expected_uid_validity="456",
                )

                self.assertTrue(result["ok"])
                self.assertIsNone(result["source"]["inReplyTo"])

    def test_legitimate_folded_token_only_references_are_preserved(self):
        raw_message = (
            b"Message-ID: <source@EXAMPLE.COM>\r\n"
            b"References: <first@EXAMPLE.COM>\r\n"
            b"\t<second@example.net>\r\n"
            b" <third@example.org>\r\n"
            b"In-Reply-To: <parent@EXAMPLE.COM>\r\n"
            b"\r\n"
            b"body"
        )
        result = imap_snapshot.read_imap_reply_source(
            RecordingMailbox(
                fetch_response=uid_fetch_response(raw_message),
            ),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["source"]["references"],
            [
                "<first@example.com>",
                "<second@example.net>",
                "<third@example.org>",
            ],
        )
        self.assertEqual(
            result["source"]["inReplyTo"],
            "<parent@example.com>",
        )

    def test_unavailable_exact_uid_is_distinct_from_unthreadable_message(self):
        result = imap_snapshot.read_imap_reply_source(
            RecordingMailbox(fetch_response=("OK", [None])),
            folder="INBOX",
            uid="123",
            expected_uid_validity="456",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "message_not_found")
        self.assertEqual(result["error"]["stage"], "message_fetch")


class ImapFolderSnapshotTests(unittest.TestCase):
    def read_snapshot(
        self,
        *,
        mailbox=None,
        messages=None,
        warnings=None,
        error=None,
        folder="INBOX",
        limit=100,
    ):
        mailbox = mailbox or RecordingMailbox(uid_search="123")
        messages = (
            [fetched_message("123")]
            if messages is None
            else messages
        )
        fetch_result = {
            "messages": messages,
            "warnings": [] if warnings is None else warnings,
            "error": error,
        }

        def fetch_messages(*arguments, **kwargs):
            mailbox.operations.append(
                (
                    "message_fetch",
                    kwargs.get("folder"),
                    kwargs.get("limit"),
                )
            )
            return fetch_result

        with patch.object(
            imap_snapshot,
            "fetch_recent_messages",
            side_effect=fetch_messages,
        ) as fetch_mock, patch.object(
            imap_snapshot,
            "to_message_preview",
            side_effect=preview_for,
        ) as preview_mock:
            result = imap_snapshot.read_imap_folder_snapshot(
                mailbox,
                folder=folder,
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
                limit=limit,
            )
        return result, mailbox, fetch_mock, preview_mock

    def test_snapshot_quotes_provider_folder_and_scopes_every_identity(self):
        mailbox = RecordingMailbox(
            uid_validity_responses=["456", "456"],
        )
        with patch.object(
            imap_snapshot,
            "resolve_custom_imap_thread_ids",
            wraps=imap_snapshot.resolve_custom_imap_thread_ids,
        ) as thread_id_mock:
            result, mailbox, fetch_mock, preview_mock = self.read_snapshot(
                mailbox=mailbox,
                folder='Team "Inbox"\\2024',
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.select_calls,
            [r'"Team \"Inbox\"\\2024"'],
        )
        fetch_mock.assert_called_once_with(
            mailbox,
            folder=r'"Team \"Inbox\"\\2024"',
            limit=100,
        )
        preview_mock.assert_called_once()
        self.assertEqual(
            mailbox.uid_calls,
            [("SEARCH", None, "ALL")],
        )
        self.assertEqual(
            mailbox.operations,
            [
                ("select", r'"Team \"Inbox\"\\2024"'),
                ("response", "UIDVALIDITY"),
                (
                    "message_fetch",
                    r'"Team \"Inbox\"\\2024"',
                    100,
                ),
                ("uid", "SEARCH", None, "ALL"),
                ("response", "UIDVALIDITY"),
            ],
        )
        self.assertEqual(
            thread_id_mock.call_args.kwargs["uid_validity"],
            "456",
        )

        snapshot = result["snapshot"]
        self.assertEqual(snapshot["serverMailboxId"], "mailbox-1")
        self.assertEqual(snapshot["providerFolder"], 'Team "Inbox"\\2024')
        self.assertEqual(snapshot["uidValidity"], "456")
        self.assertEqual(snapshot["imapUidSet"], ["123"])
        self.assertEqual(len(snapshot["messages"]), 1)
        message = snapshot["messages"][0]
        self.assertEqual(message["providerFolder"], 'Team "Inbox"\\2024')
        self.assertEqual(message["serverMailboxId"], "mailbox-1")
        self.assertEqual(message["uidValidity"], "456")
        self.assertEqual(message["imapUid"], "123")
        self.assertEqual(message["rfcMessageId"], "Local-Part@example.com")
        self.assertTrue(message["threadId"].startswith("imap:rfc:"))

        self.assertEqual(set(result["identities"]), {"123"})
        identity = result["identities"]["123"]
        self.assertEqual(identity["providerFolder"], 'Team "Inbox"\\2024')
        self.assertEqual(identity["uidValidity"], "456")
        self.assertEqual(identity["imapUid"], "123")
        self.assertEqual(identity["rfcMessageId"], "Local-Part@example.com")
        self.assertRegex(identity["fingerprint"], r"\A[0-9a-f]{64}\Z")
        self.assertNotIn("fingerprint", json.dumps(snapshot))

    def test_readonly_snapshot_keeps_both_folder_selections_readonly(self):
        mailbox = RecordingMailbox(
            uid_validity_responses=["456", "456"],
        )
        with patch.object(
            imap_snapshot,
            "fetch_recent_messages",
            return_value={
                "messages": [fetched_message("123")],
                "warnings": [],
                "error": None,
            },
        ) as fetch_mock, patch.object(
            imap_snapshot,
            "to_message_preview",
            side_effect=preview_for,
        ):
            result = imap_snapshot.read_imap_folder_snapshot(
                mailbox,
                folder="Stored Mail",
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
                readonly=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mailbox.select_readonly_calls,
            [('"Stored Mail"', True)],
        )
        fetch_mock.assert_called_once_with(
            mailbox,
            folder='"Stored Mail"',
            limit=100,
            readonly=True,
        )

    def test_preview_fetch_readonly_flag_controls_select_mode(self):
        class PreviewMailbox:
            def __init__(self):
                self.select_calls = []

            def select(self, *arguments, **kwargs):
                self.select_calls.append((arguments, kwargs))
                return "OK", [b"0"]

            def search(self, *arguments):
                return "OK", [b""]

        readonly_mailbox = PreviewMailbox()
        readonly_result = imap_connect_preview.fetch_recent_messages(
            readonly_mailbox,
            folder='"Stored Mail"',
            limit=100,
            readonly=True,
        )
        self.assertIsNone(readonly_result["error"])
        self.assertEqual(
            readonly_mailbox.select_calls,
            [(('"Stored Mail"',), {"readonly": True})],
        )

        default_mailbox = PreviewMailbox()
        default_result = imap_connect_preview.fetch_recent_messages(
            default_mailbox,
            folder='"Stored Mail"',
            limit=100,
        )
        self.assertIsNone(default_result["error"])
        self.assertEqual(
            default_mailbox.select_calls,
            [(('"Stored Mail"',), {})],
        )

    def test_snapshot_requires_exact_recent_uid_scope_and_order(self):
        cases = (
            (
                RecordingMailbox(uid_search="121 122 123"),
                [fetched_message("123"), fetched_message("121")],
                2,
            ),
            (
                RecordingMailbox(uid_search="121 122 123"),
                [fetched_message("123")],
                2,
            ),
            (
                RecordingMailbox(uid_search="121 122"),
                [fetched_message("122"), fetched_message("122")],
                100,
            ),
            (
                RecordingMailbox(uid_search=""),
                [fetched_message("123")],
                100,
            ),
        )
        for mailbox, messages, limit in cases:
            with self.subTest(uid_search=mailbox.uid_search, limit=limit):
                result, _, _, _ = self.read_snapshot(
                    mailbox=mailbox,
                    messages=messages,
                    limit=limit,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "snapshot_fetch_incomplete",
                )

    def test_uid_set_parser_rejects_malformed_or_ambiguous_provider_data(self):
        malformed_responses = (
            ("NO", [b"123"]),
            ("OK", None),
            ("OK", [b"123", b"124"]),
            ("OK", [b"123  124"]),
            ("OK", [b" 123"]),
            ("OK", [b"123 "]),
            ("OK", [b"0123"]),
            ("OK", [b"124 123"]),
            ("OK", [b"123 123"]),
            ("OK", [b"\xff"]),
        )
        for response in malformed_responses:
            with self.subTest(response=response):
                mailbox = RecordingMailbox(uid_search=response)
                result, _, _, _ = self.read_snapshot(mailbox=mailbox)
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "snapshot_uid_set_unavailable",
                )

    def test_warnings_errors_and_malformed_messages_fail_closed(self):
        cases = (
            (
                [fetched_message("123")],
                [{"code": "quota_exceeded_partial"}],
                None,
                "snapshot_fetch_incomplete",
            ),
            (
                [fetched_message("123")],
                [],
                {"code": "provider-secret"},
                "snapshot_fetch_failed",
            ),
            (
                [(message_from_bytes(RAW_MESSAGE), True, None, False)],
                [],
                None,
                "snapshot_fetch_incomplete",
            ),
            (
                [(message_from_bytes(RAW_MESSAGE), 1, "123", False)],
                [],
                None,
                "snapshot_fetch_incomplete",
            ),
            (
                [(message_from_bytes(RAW_MESSAGE), True, "123")],
                [],
                None,
                "snapshot_fetch_incomplete",
            ),
        )
        for messages, warnings, error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result, _, _, _ = self.read_snapshot(
                    messages=messages,
                    warnings=warnings,
                    error=error,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], expected_code)
                self.assertNotIn("provider-secret", json.dumps(result))

    def test_missing_fetch_contract_field_fails_closed(self):
        with patch.object(
            imap_snapshot,
            "fetch_recent_messages",
            return_value={
                "messages": [fetched_message("123")],
                "warnings": [],
            },
        ), patch.object(
            imap_snapshot,
            "to_message_preview",
            side_effect=preview_for,
        ) as preview_mock:
            result = imap_snapshot.read_imap_folder_snapshot(
                RecordingMailbox(),
                folder="INBOX",
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "snapshot_fetch_failed")
        preview_mock.assert_not_called()

    def test_preview_never_exposes_an_upstream_fingerprint(self):
        def unsafe_preview(*arguments, **kwargs):
            preview = preview_for(*arguments, **kwargs)
            preview["fingerprint"] = "must-not-leak"
            preview["rfcMessageId"] = "forged@example.com"
            return preview

        with patch.object(
            imap_snapshot,
            "fetch_recent_messages",
            return_value={
                "messages": [fetched_message("123")],
                "warnings": [],
                "error": None,
            },
        ), patch.object(
            imap_snapshot,
            "to_message_preview",
            side_effect=unsafe_preview,
        ):
            result = imap_snapshot.read_imap_folder_snapshot(
                RecordingMailbox(),
                folder="INBOX",
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
            )
        self.assertTrue(result["ok"])
        preview = result["snapshot"]["messages"][0]
        self.assertNotIn("fingerprint", preview)
        self.assertEqual(
            preview["rfcMessageId"],
            "Local-Part@example.com",
        )

    def test_changed_uidvalidity_rejects_even_an_identical_uid_scope(self):
        mailbox = RecordingMailbox(
            uid_validity_responses=["456", "457"],
            uid_search="123",
        )
        with patch.object(
            imap_snapshot,
            "resolve_custom_imap_thread_ids",
        ) as thread_id_mock:
            result, mailbox, _, preview_mock = self.read_snapshot(
                mailbox=mailbox,
                messages=[fetched_message("123")],
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "uid_validity_changed")
        self.assertIsNone(result["snapshot"])
        self.assertEqual(result["identities"], {})
        self.assertEqual(
            mailbox.uid_calls,
            [("SEARCH", None, "ALL")],
        )
        self.assertEqual(
            mailbox.response_calls,
            ["UIDVALIDITY", "UIDVALIDITY"],
        )
        preview_mock.assert_not_called()
        thread_id_mock.assert_not_called()

    def test_missing_initial_uidvalidity_fails_before_fetch(self):
        mailbox = RecordingMailbox(
            uid_validity_responses=[(None, None)],
        )
        result, mailbox, fetch_mock, preview_mock = self.read_snapshot(
            mailbox=mailbox
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "uid_validity_unavailable",
        )
        fetch_mock.assert_not_called()
        self.assertEqual(mailbox.uid_calls, [])
        self.assertEqual(mailbox.response_calls, ["UIDVALIDITY"])
        preview_mock.assert_not_called()

    def test_missing_final_uidvalidity_fails_before_serialization(self):
        mailbox = RecordingMailbox(
            uid_validity_responses=[
                "456",
                (None, None),
            ],
        )
        result, mailbox, fetch_mock, preview_mock = self.read_snapshot(
            mailbox=mailbox
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "uid_validity_unavailable",
        )
        self.assertIsNone(result["snapshot"])
        self.assertEqual(result["identities"], {})
        fetch_mock.assert_called_once()
        self.assertEqual(
            mailbox.uid_calls,
            [("SEARCH", None, "ALL")],
        )
        self.assertEqual(
            mailbox.response_calls,
            ["UIDVALIDITY", "UIDVALIDITY"],
        )
        preview_mock.assert_not_called()

    def test_inbox_and_archive_snapshots_keep_folder_uid_namespaces_separate(self):
        def fetch_for_folder(_mailbox, *, folder, limit):
            self.assertEqual(limit, 100)
            if folder == '"INBOX"':
                return {
                    "messages": [fetched_message("12")],
                    "warnings": [],
                    "error": None,
                }
            if folder == '"Stored Mail"':
                return {
                    "messages": [
                        fetched_message("91", message_id="archive@example.com")
                    ],
                    "warnings": [],
                    "error": None,
                }
            raise AssertionError(f"unexpected folder {folder}")

        with patch.object(
            imap_snapshot,
            "fetch_recent_messages",
            side_effect=fetch_for_folder,
        ), patch.object(
            imap_snapshot,
            "to_message_preview",
            side_effect=preview_for,
        ):
            inbox = imap_snapshot.read_imap_folder_snapshot(
                RecordingMailbox(uid_validity="41", uid_search="12"),
                folder="INBOX",
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
            )
            archive = imap_snapshot.read_imap_folder_snapshot(
                RecordingMailbox(uid_validity="99", uid_search="91"),
                folder="Stored Mail",
                mailbox_key="mailbox-1",
                email_address="owner@example.com",
            )

        self.assertTrue(inbox["ok"])
        self.assertTrue(archive["ok"])
        self.assertEqual(inbox["snapshot"]["imapUidSet"], ["12"])
        self.assertEqual(archive["snapshot"]["imapUidSet"], ["91"])
        self.assertEqual(inbox["snapshot"]["uidValidity"], "41")
        self.assertEqual(archive["snapshot"]["uidValidity"], "99")
        self.assertEqual(
            inbox["snapshot"]["messages"][0]["providerFolder"],
            "INBOX",
        )
        self.assertEqual(
            archive["snapshot"]["messages"][0]["providerFolder"],
            "Stored Mail",
        )
        self.assertNotEqual(
            inbox["snapshot"]["messages"][0]["threadId"],
            archive["snapshot"]["messages"][0]["threadId"],
        )
        self.assertEqual(set(inbox["identities"]), {"12"})
        self.assertEqual(set(archive["identities"]), {"91"})

    def test_invalid_context_or_limit_stops_before_fetch(self):
        invalid_cases = (
            {"mailbox_key": "", "email_address": "owner@example.com", "limit": 1},
            {"mailbox_key": "box", "email_address": "", "limit": 1},
            {"mailbox_key": "box", "email_address": "owner@example.com", "limit": 0},
            {"mailbox_key": "box", "email_address": "owner@example.com", "limit": 101},
            {"mailbox_key": "box", "email_address": "owner@example.com", "limit": True},
        )
        for case in invalid_cases:
            with self.subTest(case=case), patch.object(
                imap_snapshot,
                "fetch_recent_messages",
            ) as fetch_mock:
                result = imap_snapshot.read_imap_folder_snapshot(
                    RecordingMailbox(),
                    folder="INBOX",
                    **case,
                )
                self.assertFalse(result["ok"])
                fetch_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
