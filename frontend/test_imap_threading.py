import importlib
import imaplib
import re
import sys
import unittest
from email import message_from_string
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote_to_bytes

FRONTEND_DIR = Path(__file__).resolve().parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import imap_connect_preview


def threading_record(
    message_id=None,
    *,
    in_reply_to=None,
    references=None,
    uid=None,
    message_id_ambiguous=False,
    fallback_id="fallback-id",
):
    return {
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": list(references or []),
        "imap_uid": uid,
        "message_id_ambiguous": message_id_ambiguous,
        "fallback_id": fallback_id,
    }


def resolve(records, uid_validity="77"):
    return imap_connect_preview.resolve_custom_imap_thread_ids(
        records,
        mailbox_key="mailbox-1",
        folder="INBOX",
        uid_validity=uid_validity,
    )


def rfc_thread_id(message_id, mailbox_key="mailbox-1", folder="INBOX"):
    return imap_connect_preview.build_bounded_thread_identity(
        "imap:rfc",
        mailbox_key,
        message_id,
    )


def uid_thread_id(uid, uid_validity="77", mailbox_key="mailbox-1", folder="INBOX"):
    return imap_connect_preview.build_bounded_thread_identity(
        "imap:uid",
        mailbox_key,
        folder,
        uid_validity,
        uid,
    )


class MessageIdNormalizationTests(unittest.TestCase):
    def test_normalizes_brackets_whitespace_and_domain_case(self):
        self.assertEqual(
            imap_connect_preview.normalize_message_id_token("  <Local.Part@EXAMPLE.COM>  "),
            "Local.Part@example.com",
        )

    def test_folded_and_malformed_references_are_parsed_conservatively_in_order(self):
        references = (
            "noise <root@example.com>\r\n\t<child@EXAMPLE.COM> "
            "invalid,token <bad\x01@example.com>"
        )
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(references),
            ["root@example.com", "child@example.com"],
        )

    def test_prose_bare_email_is_not_a_message_id(self):
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(
                "Please contact support@example.com for help"
            ),
            [],
        )


class FolderIndependentRfcIdentityTests(unittest.TestCase):
    def test_rfc_conversation_identity_survives_folder_difference(self):
        root = threading_record("root@example.com", uid="1")
        reply = threading_record(
            "reply@example.com",
            in_reply_to="root@example.com",
            references=["root@example.com"],
            uid="2",
        )
        inbox_ids = imap_connect_preview.resolve_custom_imap_thread_ids(
            [root, reply],
            mailbox_key="mailbox-1",
            folder="INBOX",
            uid_validity="77",
        )
        archive_ids = imap_connect_preview.resolve_custom_imap_thread_ids(
            [root, reply],
            mailbox_key="mailbox-1",
            folder="Archive",
            uid_validity="88",
        )

        self.assertEqual(inbox_ids, archive_ids)

    def test_bracketed_id_ignores_bare_email_in_surrounding_prose(self):
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(
                "Previous message <root@example.com> and contact support@example.com"
            ),
            ["root@example.com"],
        )

    def test_whole_field_bare_compatibility_id_is_accepted_but_multiple_are_not(self):
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens("root@EXAMPLE.COM"),
            ["root@example.com"],
        )
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(
                "root@example.com another@example.com"
            ),
            [],
        )

    def test_control_characters_and_oversized_tokens_are_rejected(self):
        self.assertIsNone(
            imap_connect_preview.normalize_message_id_token("<bad\x01@example.com>")
        )
        oversized = "x" * imap_connect_preview.MAX_MESSAGE_ID_TOKEN_LENGTH + "@example.com"
        self.assertIsNone(imap_connect_preview.normalize_message_id_token(oversized))

    def test_extracts_all_three_rfc_threading_headers_without_exposing_them(self):
        message = message_from_string(
            "Message-ID: <child@EXAMPLE.COM>\n"
            "In-Reply-To: <parent@example.com>\n"
            "References: <root@example.com>\n\t<parent@example.com>\n"
            "Subject: Same subject\n\nBody"
        )
        metadata = imap_connect_preview.extract_message_thread_metadata(
            message,
            "42",
            "child@EXAMPLE.COM",
        )
        self.assertEqual(metadata["message_id"], "child@example.com")
        self.assertEqual(metadata["in_reply_to"], "parent@example.com")
        self.assertEqual(
            metadata["references"],
            ["root@example.com", "parent@example.com"],
        )

    def test_multiple_header_instances_preserve_instance_and_folded_token_order(self):
        message = message_from_string(
            "Message-ID: <child@example.com>\n"
            "In-Reply-To: Earlier <parent-1@example.com>\n"
            "In-Reply-To: <parent-2@example.com>\n\t<parent-3@example.com>\n"
            "References: <root-1@example.com>\n\t<middle@example.com>\n"
            "References: Later <root-2@example.com> and support@example.com\n\nBody"
        )
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(
                message.get_all("In-Reply-To", [])
            ),
            [
                "parent-1@example.com",
                "parent-2@example.com",
                "parent-3@example.com",
            ],
        )
        metadata = imap_connect_preview.extract_message_thread_metadata(
            message,
            "42",
            "child@example.com",
        )
        self.assertEqual(metadata["in_reply_to"], "parent-1@example.com")
        self.assertEqual(
            metadata["references"],
            [
                "root-1@example.com",
                "middle@example.com",
                "root-2@example.com",
            ],
        )

    def test_multiple_different_own_message_id_headers_are_ambiguous(self):
        message = message_from_string(
            "Message-ID: <first@example.com>\n"
            "Message-ID: second@example.com\n\nBody"
        )
        metadata = imap_connect_preview.extract_message_thread_metadata(
            message,
            "42",
            "fallback",
        )
        self.assertIsNone(metadata["message_id"])
        self.assertTrue(metadata["message_id_ambiguous"])
        self.assertEqual(resolve([metadata]), [uid_thread_id("42")])

    def test_header_instance_count_and_total_input_are_fail_closed(self):
        too_many = [
            f"<message-{index}@example.com>"
            for index in range(imap_connect_preview.MAX_MESSAGE_ID_HEADER_INSTANCES + 1)
        ]
        self.assertEqual(imap_connect_preview.parse_message_id_tokens(too_many), [])

        oversized = [
            "<root@example.com>",
            "x" * imap_connect_preview.MAX_MESSAGE_ID_HEADER_LENGTH,
        ]
        self.assertEqual(imap_connect_preview.parse_message_id_tokens(oversized), [])

    def test_stateful_bracket_parser_accepts_only_balanced_or_complete_bare_fields(self):
        accepted = {
            "<root@example.com>": ["root@example.com"],
            "Previous <root@example.com> next <child@example.com>": [
                "root@example.com",
                "child@example.com",
            ],
            "root@example.com": ["root@example.com"],
        }
        rejected = [
            "<<root@example.com>>",
            "<broken@example.com <root@example.com>",
            "<root@example.com",
            "root@example.com>",
            "Please contact root@example.com",
            "<root@example.com> broken <child@example.com",
        ]

        for header, expected in accepted.items():
            with self.subTest(header=header):
                self.assertEqual(
                    imap_connect_preview.parse_message_id_tokens(header),
                    expected,
                )

        for header in rejected:
            with self.subTest(header=header):
                self.assertEqual(
                    imap_connect_preview.parse_message_id_tokens(header),
                    [],
                )

    def test_malformed_header_instance_is_ignored_without_poisoning_valid_instances(self):
        self.assertEqual(
            imap_connect_preview.parse_message_id_tokens(
                [
                    "<<malformed@example.com>>",
                    "Valid <root@example.com> next <child@example.com>",
                ]
            ),
            ["root@example.com", "child@example.com"],
        )

    def test_nested_references_and_in_reply_to_instances_never_salvage_inner_ids(self):
        message = message_from_string(
            "Message-ID: <child@example.com>\n"
            "References: <<bad-root@example.com>>\n"
            "References: <valid-root@example.com>\n"
            "In-Reply-To: <broken@example.com <bad-parent@example.com>\n"
            "In-Reply-To: <valid-parent@example.com>\n\nBody"
        )
        metadata = imap_connect_preview.extract_message_thread_metadata(
            message,
            "42",
            "fallback",
        )

        self.assertEqual(metadata["references"], ["valid-root@example.com"])
        self.assertEqual(metadata["in_reply_to"], "valid-parent@example.com")


class CustomImapThreadResolutionTests(unittest.TestCase):
    def test_same_sender_recipients_and_subject_with_distinct_message_ids_stay_separate(self):
        ids = resolve(
            [
                threading_record("first@example.com", uid="1"),
                threading_record("second@example.com", uid="2"),
            ]
        )
        self.assertEqual(
            ids,
            [rfc_thread_id("first@example.com"), rfc_thread_id("second@example.com")],
        )

    def test_eight_repeated_submissions_receive_eight_thread_ids(self):
        ids = resolve(
            [threading_record(f"submission-{index}@example.com", uid=str(index + 1)) for index in range(8)]
        )
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)

    def test_direct_reply_uses_parent_root(self):
        ids = resolve(
            [
                threading_record("root@example.com", uid="1"),
                threading_record(
                    "reply@example.com",
                    in_reply_to="root@example.com",
                    uid="2",
                ),
            ]
        )
        self.assertEqual(ids, [rfc_thread_id("root@example.com")] * 2)

    def test_references_chain_uses_first_reference(self):
        ids = resolve(
            [
                threading_record("root@example.com", uid="1"),
                threading_record(
                    "reply@example.com",
                    in_reply_to="root@example.com",
                    references=["root@example.com"],
                    uid="2",
                ),
                threading_record(
                    "reply-2@example.com",
                    in_reply_to="reply@example.com",
                    references=["root@example.com", "reply@example.com"],
                    uid="3",
                ),
            ]
        )
        self.assertEqual(ids, [rfc_thread_id("root@example.com")] * 3)

    def test_multi_hop_in_reply_to_chain_resolves_transitively(self):
        ids = resolve(
            [
                threading_record("root@example.com", uid="1"),
                threading_record(
                    "middle@example.com",
                    in_reply_to="root@example.com",
                    uid="2",
                ),
                threading_record(
                    "leaf@example.com",
                    in_reply_to="middle@example.com",
                    uid="3",
                ),
            ]
        )
        self.assertEqual(ids, [rfc_thread_id("root@example.com")] * 3)

    def test_child_before_parent_and_reversed_batch_resolve_identically(self):
        root = threading_record("root@example.com", uid="1")
        child = threading_record(
            "child@example.com",
            in_reply_to="root@example.com",
            uid="2",
        )
        self.assertEqual(resolve([child, root]), [rfc_thread_id("root@example.com")] * 2)
        self.assertEqual(resolve([root, child]), [rfc_thread_id("root@example.com")] * 2)

    def test_parent_outside_batch_uses_direct_parent_id(self):
        ids = resolve(
            [
                threading_record(
                    "child@example.com",
                    in_reply_to="outside-parent@example.com",
                    uid="7",
                )
            ]
        )
        self.assertEqual(ids, [rfc_thread_id("outside-parent@example.com")])

    def test_missing_message_id_uses_mailbox_folder_uidvalidity_and_uid(self):
        ids = resolve([threading_record(uid="42")], uid_validity="900")
        self.assertEqual(ids, [uid_thread_id("42", uid_validity="900")])

    def test_same_uid_with_different_uidvalidity_is_distinct(self):
        record = threading_record(uid="42")
        first = resolve([record], uid_validity="900")[0]
        second = resolve([record], uid_validity="901")[0]
        self.assertNotEqual(first, second)

    def test_cycle_is_bounded_and_falls_back_to_each_direct_parent(self):
        ids = resolve(
            [
                threading_record("a@example.com", in_reply_to="b@example.com", uid="1"),
                threading_record("b@example.com", in_reply_to="a@example.com", uid="2"),
            ]
        )
        self.assertEqual(
            ids,
            [rfc_thread_id("b@example.com"), rfc_thread_id("a@example.com")],
        )

    def test_missing_uid_or_uidvalidity_without_rfc_identity_fails_closed(self):
        self.assertEqual(resolve([threading_record(uid=None)], uid_validity="900"), [None])
        self.assertEqual(resolve([threading_record(uid="42")], uid_validity=None), [None])
        self.assertEqual(
            resolve([threading_record(), threading_record()], uid_validity=None),
            [None, None],
        )

    def test_duplicate_standalone_message_ids_use_distinct_uid_fallbacks(self):
        records = [
            threading_record("duplicate@example.com", uid="1"),
            threading_record("duplicate@example.com", uid="2"),
        ]
        self.assertEqual(
            resolve(records),
            [uid_thread_id("1"), uid_thread_id("2")],
        )

    def test_ambiguous_parent_and_child_are_order_independent_uid_fallbacks(self):
        first_parent = threading_record(
            "duplicate@example.com",
            in_reply_to="root-a@example.com",
            uid="1",
        )
        second_parent = threading_record(
            "duplicate@example.com",
            in_reply_to="root-b@example.com",
            uid="2",
        )
        child = threading_record(
            "child@example.com",
            in_reply_to="duplicate@example.com",
            uid="3",
        )
        expected = [uid_thread_id("1"), uid_thread_id("2"), uid_thread_id("3")]
        self.assertEqual(resolve([first_parent, second_parent, child]), expected)
        reversed_ids = resolve([second_parent, first_parent, child])
        self.assertEqual(
            reversed_ids,
            [uid_thread_id("2"), uid_thread_id("1"), uid_thread_id("3")],
        )

    def test_ambiguous_references_root_and_duplicate_cycle_use_uid_fallbacks(self):
        records = [
            threading_record(
                "duplicate@example.com",
                in_reply_to="duplicate@example.com",
                uid="1",
            ),
            threading_record(
                "duplicate@example.com",
                in_reply_to="duplicate@example.com",
                uid="2",
            ),
            threading_record(
                "child@example.com",
                references=["duplicate@example.com"],
                uid="3",
            ),
        ]
        self.assertEqual(
            resolve(records),
            [uid_thread_id("1"), uid_thread_id("2"), uid_thread_id("3")],
        )

    def test_same_rfc_root_is_scoped_by_mailbox_but_not_folder(self):
        record = threading_record("root@example.com", uid="1")
        mailbox_a = resolve([record], uid_validity="77")[0]
        mailbox_b = imap_connect_preview.resolve_custom_imap_thread_ids(
            [record],
            mailbox_key="mailbox-2",
            folder="INBOX",
            uid_validity="77",
        )[0]
        archive = imap_connect_preview.resolve_custom_imap_thread_ids(
            [record],
            mailbox_key="mailbox-1",
            folder="Archive",
            uid_validity="77",
        )[0]
        self.assertEqual(mailbox_a, rfc_thread_id("root@example.com"))
        self.assertNotEqual(mailbox_a, mailbox_b)
        self.assertEqual(mailbox_a, archive)
        self.assertEqual(resolve([record])[0], resolve([record])[0])

    def test_long_scoped_rfc_identity_is_bounded_and_hashes_complete_scope(self):
        root = f"{'r' * 460}@example.com"
        first = imap_connect_preview.build_bounded_thread_identity(
            "imap:rfc",
            "mailbox-" + "m" * 300,
            root,
        )
        repeated = imap_connect_preview.build_bounded_thread_identity(
            "imap:rfc",
            "mailbox-" + "m" * 300,
            root,
        )
        changed_tail = imap_connect_preview.build_bounded_thread_identity(
            "imap:rfc",
            "mailbox-" + "m" * 299 + "n",
            root,
        )
        self.assertLessEqual(len(first), imap_connect_preview.MAX_THREAD_ID_LENGTH)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed_tail)

    def test_long_unicode_scoped_identities_use_only_complete_percent_encoding(self):
        cases = [
            ("é" * 180, "root@example.com"),
            ("mailbox-1", f"{'é' * 180}@example.com"),
            ("é" * 100, "root@example.com"),
            ("mailbox-1", f"{'é' * 100}@example.com"),
        ]

        for mailbox_key, root in cases:
            with self.subTest(mailbox_key=mailbox_key[:12], root=root[:12]):
                identity = imap_connect_preview.build_bounded_thread_identity(
                    "imap:rfc",
                    mailbox_key,
                    root,
                )
                self.assertLessEqual(
                    len(identity),
                    imap_connect_preview.MAX_THREAD_ID_LENGTH,
                )
                self.assertIsNone(re.search(r"%(?![0-9A-Fa-f]{2})", identity))
                self.assertIsNone(re.search(r"%[0-9A-Fa-f](?![0-9A-Fa-f])", identity))
                self.assertFalse(any(ord(character) < 32 for character in identity))
                for encoded_component in identity.split(":")[2:]:
                    unquote_to_bytes(encoded_component).decode("utf-8", errors="strict")
                self.assertEqual(
                    identity,
                    imap_connect_preview.build_bounded_thread_identity(
                        "imap:rfc",
                        mailbox_key,
                        root,
                    ),
                )

    def test_long_identity_hash_suffix_uses_complete_untruncated_scope(self):
        first = imap_connect_preview.build_bounded_thread_identity(
            "imap:rfc",
            "é" * 180,
            "資料夾" * 180,
            "root@example.com",
        )
        second = imap_connect_preview.build_bounded_thread_identity(
            "imap:rfc",
            "é" * 179 + "ê",
            "資料夾" * 180,
            "root@example.com",
        )
        first_suffix = first.rsplit("~", 1)[1]
        second_suffix = second.rsplit("~", 1)[1]

        self.assertRegex(first_suffix, r"^[0-9a-f]{24}$")
        self.assertRegex(second_suffix, r"^[0-9a-f]{24}$")
        self.assertNotEqual(first_suffix, second_suffix)


class CustomImapPreviewIntegrationTests(unittest.TestCase):
    def build_preview(self, message, uid, uidvalidity_response):
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [str(uid or "").encode()])
        mailbox.response.return_value = uidvalidity_response
        with patch.object(
            imap_connect_preview,
            "open_mailbox_connection",
            return_value=mailbox,
        ), patch.object(
            imap_connect_preview,
            "fetch_recent_messages",
            return_value={
                "messages": [(message, True, uid, False)],
                "warnings": [],
                "error": None,
            },
        ), patch.object(
            imap_connect_preview,
            "resolve_preview_routing",
            return_value={"ui_signal": "NEW", "internalClassification": "unknown"},
        ):
            return imap_connect_preview.build_connect_preview_response(
                {
                    "provider": "custom_imap",
                    "mailboxId": "mailbox-1",
                    "email": "demo@example.com",
                    "password": "mock-only",
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "demo@example.com",
                    "folder": "INBOX",
                }
            )

    def test_custom_imap_response_adds_thread_ids_without_raw_headers(self):
        messages = [
            message_from_string(
                "Message-ID: <first@example.com>\n"
                "From: Website <forms@example.com>\n"
                "To: demo@example.com\n"
                "Subject: Repeated subject\n\nFirst"
            ),
            message_from_string(
                "Message-ID: <second@example.com>\n"
                "From: Website <forms@example.com>\n"
                "To: demo@example.com\n"
                "Subject: Repeated subject\n\nSecond"
            ),
        ]
        mailbox = Mock()
        mailbox.uid.return_value = ("OK", [b"1 2"])
        mailbox.response.return_value = ("UIDVALIDITY", [b"77"])

        with patch.object(
            imap_connect_preview,
            "open_mailbox_connection",
            return_value=mailbox,
        ), patch.object(
            imap_connect_preview,
            "fetch_recent_messages",
            return_value={
                "messages": [
                    (messages[0], True, "1", False),
                    (messages[1], False, "2", True),
                ],
                "warnings": [],
                "error": None,
            },
        ), patch.object(
            imap_connect_preview,
            "resolve_preview_routing",
            return_value={
                "ui_signal": "DEMO",
                "internalClassification": "demo",
                "classifierVersion": "test",
            },
        ):
            status, payload = imap_connect_preview.build_connect_preview_response(
                {
                    "provider": "custom_imap",
                    "mailboxId": "mailbox-1",
                    "email": "demo@example.com",
                    "password": "mock-only",
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "demo@example.com",
                    "folder": "INBOX",
                }
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["uidValidity"], "77")
        self.assertEqual(
            [message["threadId"] for message in payload["messages"]],
            [rfc_thread_id("first@example.com"), rfc_thread_id("second@example.com")],
        )
        for preview in payload["messages"]:
            self.assertNotIn("messageId", preview)
            self.assertNotIn("inReplyTo", preview)
            self.assertNotIn("references", preview)

    def test_headerless_message_with_uid_metadata_uses_uid_identity(self):
        message = message_from_string("Subject: Headerless\n\nBody")
        status, payload = self.build_preview(
            message,
            "42",
            ("UIDVALIDITY", [b"900"]),
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [preview["threadId"] for preview in payload["messages"]],
            [uid_thread_id("42", uid_validity="900")],
        )

    def test_unidentifiable_message_is_omitted_without_sensitive_log_content(self):
        message = message_from_string(
            "From: Secret Person <secret-address@example.com>\n"
            "Subject: TOP SECRET SUBJECT\n\nPRIVATE BODY CONTENT"
        )
        with self.assertLogs(imap_connect_preview.logger.name, level="WARNING") as logs:
            status, payload = self.build_preview(
                message,
                None,
                ("UIDVALIDITY", [b"900"]),
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["messages"], [])
        log_output = "\n".join(logs.output)
        self.assertNotIn("secret-address@example.com", log_output)
        self.assertNotIn("TOP SECRET SUBJECT", log_output)
        self.assertNotIn("PRIVATE BODY CONTENT", log_output)

    def test_headerless_message_without_uidvalidity_is_omitted(self):
        message = message_from_string("Subject: Headerless\n\nBody")
        with self.assertLogs(imap_connect_preview.logger.name, level="WARNING"):
            status, payload = self.build_preview(
                message,
                "42",
                ("FAILED", []),
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["messages"], [])

    def test_shared_gmail_preview_serializer_remains_thread_neutral(self):
        message = message_from_string(
            "Message-ID: <gmail-message@example.com>\n"
            "From: Sender <sender@example.com>\n"
            "To: owner@example.com\n"
            "Subject: Subject\n\nBody"
        )
        with patch.object(
            imap_connect_preview,
            "resolve_preview_routing",
            return_value={"ui_signal": "NEW", "internalClassification": "unknown"},
        ):
            preview = imap_connect_preview.to_message_preview(
                message,
                0,
                "owner@example.com",
                True,
                "gmail-provider-id",
            )
        self.assertNotIn("threadId", preview)


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_open_imap_or_network_connections(self):
        with patch.object(imaplib, "IMAP4") as imap, patch.object(
            imaplib,
            "IMAP4_SSL",
        ) as imap_ssl:
            importlib.reload(imap_connect_preview)
        imap.assert_not_called()
        imap_ssl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
