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
        self.response_calls: list[str] = []
        self.uid_calls: list[tuple] = []
        self.unsafe_calls: list[tuple] = []
        self.operations: list[tuple] = []

    @staticmethod
    def _resolve(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def select(self, folder):
        self.select_calls.append(folder)
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
