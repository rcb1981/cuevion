from __future__ import annotations

import importlib.util
import imaplib
import sys
import types
import unittest
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import Mock, patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


def _unexpected_provider_call(*_args, **_kwargs):
    raise AssertionError("unexpected provider call")


def _error_payload(code, message):
    return {"error": {"code": code, "message": message}}


def _reject_unknown_fields(payload, allowed_fields):
    return (
        _error_payload("invalid_fields", "Unexpected request fields.")
        if set(payload) - set(allowed_fields)
        else None
    )


authenticated_imap_stub = types.ModuleType("authenticated_imap")
authenticated_imap_stub.find_forbidden_custom_request_fields = (
    lambda _payload: False
)
authenticated_imap_stub.resolve_authenticated_imap_mailbox = (
    _unexpected_provider_call
)

smtp_connection_stub = types.ModuleType("smtp_connection")
smtp_connection_stub.SmtpConnectionError = type(
    "SmtpConnectionError",
    (Exception,),
    {},
)
smtp_connection_stub.send_public_smtp_message = _unexpected_provider_call

authenticated_gmail_stub = types.ModuleType("authenticated_gmail")
authenticated_gmail_stub.MAX_GMAIL_RESPONSE_BYTES = 1
authenticated_gmail_stub.MAX_SEND_REQUEST_BODY_BYTES = 1
authenticated_gmail_stub.error_payload = _error_payload
authenticated_gmail_stub.reject_unknown_fields = _reject_unknown_fields
authenticated_gmail_stub.valid_identifier = (
    lambda value: isinstance(value, str)
    and bool(value)
    and "\r" not in value
    and "\n" not in value
)
for dependency_name in (
    "gmail_http_error_code",
    "read_bounded_response",
    "read_json_body",
    "refresh_gmail_context",
    "resolve_gmail_context",
    "resolve_owned_mailbox",
    "send_json",
    "send_method_not_allowed",
):
    setattr(authenticated_gmail_stub, dependency_name, _unexpected_provider_call)

imap_snapshot_stub = types.ModuleType("inboxes.imap_snapshot")
imap_snapshot_stub.read_imap_reply_source = _unexpected_provider_call

MODULE_SPEC = importlib.util.spec_from_file_location(
    "send_gmail_api_under_test",
    CURRENT_DIR / "send-gmail.py",
)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("Could not load the mailbox sending endpoint.")
send_gmail = importlib.util.module_from_spec(MODULE_SPEC)
with patch.dict(
    sys.modules,
    {
        "authenticated_imap": authenticated_imap_stub,
        "authenticated_gmail": authenticated_gmail_stub,
        "inboxes.imap_snapshot": imap_snapshot_stub,
        "smtp_connection": smtp_connection_stub,
    },
):
    MODULE_SPEC.loader.exec_module(send_gmail)


VALID_CONTEXT = {
    "sourceProviderFolder": "INBOX",
    "sourceImapUid": "42",
    "sourceUidValidity": "9001",
}
CUSTOM_MAILBOX = {
    "email": "owner@example.com",
    "imap": {
        "host": "imap.example.com",
        "port": 993,
        "ssl": True,
        "username": "owner@example.com",
        "password": "stored-imap-secret",
    },
    "smtp": {
        "host": "smtp.example.com",
        "port": 465,
        "security": "ssl",
        "username": "owner@example.com",
        "password": "stored-smtp-secret",
    },
}


def _base_payload(**updates):
    payload = {
        "mailboxId": "custom-mailbox-1",
        "to": "sender@example.com",
        "subject": "Re: Original topic",
        "bodyText": "Reply body.",
    }
    payload.update(updates)
    return payload


def _successful_source(**updates):
    source = {
        "providerFolder": "INBOX",
        "imapUid": "42",
        "uidValidity": "9001",
        "messageId": "<source@example.com>",
        "references": ["<root@example.com>"],
        "inReplyTo": None,
    }
    source.update(updates)
    return {
        "ok": True,
        "status": "ok",
        "source": source,
        "error": None,
    }


def _failed_source(code):
    return {
        "ok": False,
        "status": "error",
        "source": None,
        "error": {"code": code, "message": "provider detail", "stage": "test"},
    }


def _smtp_error(code):
    error = send_gmail.SmtpConnectionError(code)
    error.code = code
    return error


class _RequestHarness:
    def __init__(
        self,
        payload,
        *,
        provider="custom_imap",
        source_result=None,
        open_side_effect=None,
        read_side_effect=None,
        smtp_side_effect=None,
    ):
        self.payload = payload
        self.connection = Mock(name="imap_connection")
        self.responses = []
        self.owned = {
            "status": "ok",
            "inbox": {"provider": provider},
        }
        self.resolved = {
            "status": "ok",
            "mailbox": CUSTOM_MAILBOX,
            "error": None,
        }
        self.source_result = source_result or _successful_source()
        self.open_side_effect = open_side_effect
        self.read_side_effect = read_side_effect
        self.smtp_side_effect = smtp_side_effect

    def run(self):
        def capture_response(_handler, status, payload):
            self.responses.append((status, payload))

        open_kwargs = {"return_value": self.connection}
        if self.open_side_effect is not None:
            open_kwargs = {"side_effect": self.open_side_effect}
        read_kwargs = {"return_value": self.source_result}
        if self.read_side_effect is not None:
            read_kwargs = {"side_effect": self.read_side_effect}

        with patch.object(
            send_gmail,
            "read_json_body",
            return_value=(self.payload, None),
        ) as self.read_body_mock, patch.object(
            send_gmail,
            "resolve_owned_mailbox",
            return_value=self.owned,
        ) as self.owned_mock, patch.object(
            send_gmail,
            "resolve_authenticated_imap_mailbox",
            return_value=self.resolved,
        ) as self.resolved_mock, patch.object(
            send_gmail,
            "resolve_gmail_context",
            side_effect=AssertionError("unexpected Gmail context resolution"),
        ) as self.gmail_context_mock, patch.object(
            send_gmail,
            "_open_custom_imap_connection",
            **open_kwargs,
        ) as self.open_mock, patch.object(
            send_gmail,
            "read_imap_reply_source",
            **read_kwargs,
        ) as self.source_mock, patch.object(
            send_gmail,
            "send_public_smtp_message",
            side_effect=self.smtp_side_effect,
        ) as self.smtp_mock, patch.object(
            send_gmail,
            "send_json",
            side_effect=capture_response,
        ) as self.send_json_mock:
            request = types.SimpleNamespace(headers={"Authorization": "Bearer test"})
            send_gmail.handler._handle_post(request)

        if len(self.responses) != 1:
            raise AssertionError(f"Expected one response, got {self.responses!r}")
        return self.responses[0]


class CustomSmtpReplyContinuityTests(unittest.TestCase):
    def test_imap_context_requires_exact_canonical_fields(self):
        context, error = send_gmail._validate_imap_reply_context(
            {"imapReplyContext": VALID_CONTEXT}
        )
        self.assertEqual(context, VALID_CONTEXT)
        self.assertIsNone(error)

        invalid_contexts = (
            None,
            {},
            {**VALID_CONTEXT, "extra": "value"},
            {**VALID_CONTEXT, "sourceProviderFolder": ""},
            {**VALID_CONTEXT, "sourceProviderFolder": " INBOX"},
            {**VALID_CONTEXT, "sourceProviderFolder": "INBOX\r\nBcc: x@y.test"},
            {**VALID_CONTEXT, "sourceImapUid": "0"},
            {**VALID_CONTEXT, "sourceImapUid": "01"},
            {**VALID_CONTEXT, "sourceImapUid": 42},
            {**VALID_CONTEXT, "sourceImapUid": "4294967296"},
            {**VALID_CONTEXT, "sourceUidValidity": "+9001"},
            {**VALID_CONTEXT, "sourceUidValidity": "4294967296"},
        )
        for invalid_context in invalid_contexts:
            with self.subTest(context=invalid_context):
                context, error = send_gmail._validate_imap_reply_context(
                    {"imapReplyContext": invalid_context}
                )
                self.assertIsNone(context)
                self.assertEqual(
                    error["error"]["code"],
                    "invalid_imap_reply_context",
                )

    def test_absent_imap_context_is_allowed(self):
        self.assertEqual(
            send_gmail._validate_imap_reply_context({}),
            (None, None),
        )

    def test_unknown_top_level_field_is_rejected_before_ownership_or_network(self):
        harness = _RequestHarness(_base_payload(unexpected=True))
        status, response = harness.run()
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "forbidden_connection_fields")
        harness.owned_mock.assert_not_called()
        harness.resolved_mock.assert_not_called()
        harness.open_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_valid_context_is_excluded_from_connection_field_scanning(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
        )
        with patch.object(
            send_gmail,
            "find_forbidden_custom_request_fields",
            side_effect=lambda value: "imapReplyContext" in value,
        ) as forbidden_mock:
            status, response = harness.run()

        self.assertEqual((status, response), (200, {"ok": True}))
        forbidden_mock.assert_called_once()
        self.assertNotIn("imapReplyContext", forbidden_mock.call_args.args[0])

    def test_ownership_failure_stops_before_imap_or_smtp(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
        )
        harness.owned = {
            "status": "error",
            "status_code": 403,
            "error": _error_payload("mailbox_not_owned", "Not owned."),
        }

        status, response = harness.run()

        self.assertEqual(status, 403)
        self.assertEqual(response["error"]["code"], "mailbox_not_owned")
        harness.resolved_mock.assert_not_called()
        harness.open_mock.assert_not_called()
        harness.source_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_gmail_rejects_imap_context_before_any_provider_operation(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            provider="google",
        )
        status, response = harness.run()
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "invalid_imap_reply_context")
        harness.gmail_context_mock.assert_not_called()
        harness.resolved_mock.assert_not_called()
        harness.open_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_custom_imap_rejects_gmail_context_before_provider_operations(self):
        harness = _RequestHarness(
            _base_payload(
                replyContext={"sourceProviderMessageId": "gmail-message-1"}
            )
        )
        status, response = harness.run()
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "invalid_reply_context")
        harness.resolved_mock.assert_not_called()
        harness.open_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_custom_non_reply_gets_identity_headers_but_no_thread_headers(self):
        for subject in ("Re: Looks like a reply", "Fwd: Original topic", "New topic"):
            with self.subTest(subject=subject):
                harness = _RequestHarness(_base_payload(subject=subject))
                status, response = harness.run()
                self.assertEqual((status, response), (200, {"ok": True}))
                harness.open_mock.assert_not_called()
                harness.source_mock.assert_not_called()
                harness.smtp_mock.assert_called_once()
                message = harness.smtp_mock.call_args.args[5]
                parsed = BytesParser(policy=policy.default).parsebytes(
                    message.as_bytes()
                )
                self.assertIsNotNone(parsedate_to_datetime(parsed.get("Date")))
                self.assertIsNotNone(
                    send_gmail._normalize_rfc_message_id(parsed.get("Message-ID"))
                )
                self.assertIsNone(parsed.get("In-Reply-To"))
                self.assertIsNone(parsed.get("References"))

    def test_reply_and_reply_all_share_exact_source_ancestry(self):
        ancestry = []
        for compose_mode in ("reply", "reply_all"):
            with self.subTest(compose_mode=compose_mode):
                harness = _RequestHarness(
                    _base_payload(
                        cc="other@example.com",
                        imapReplyContext=VALID_CONTEXT,
                    )
                )
                status, response = harness.run()
                self.assertEqual((status, response), (200, {"ok": True}))
                harness.open_mock.assert_called_once_with(CUSTOM_MAILBOX)
                harness.source_mock.assert_called_once_with(
                    harness.connection,
                    folder="INBOX",
                    uid="42",
                    expected_uid_validity="9001",
                )
                harness.connection.close.assert_not_called()
                harness.connection.logout.assert_called_once_with()
                harness.smtp_mock.assert_called_once()
                message = harness.smtp_mock.call_args.args[5]
                ancestry.append(
                    (str(message["In-Reply-To"]), str(message["References"]))
                )
                self.assertIsNotNone(message["Date"])
                self.assertIsNotNone(message["Message-ID"])

        self.assertEqual(
            ancestry,
            [
                (
                    "<source@example.com>",
                    "<root@example.com> <source@example.com>",
                ),
                (
                    "<source@example.com>",
                    "<root@example.com> <source@example.com>",
                ),
            ],
        )

    def test_simple_source_message_id_becomes_both_reply_headers(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            source_result=_successful_source(references=[], inReplyTo=None),
        )

        status, response = harness.run()

        self.assertEqual((status, response), (200, {"ok": True}))
        message = harness.smtp_mock.call_args.args[5]
        self.assertEqual(str(message["In-Reply-To"]), "<source@example.com>")
        self.assertEqual(str(message["References"]), "<source@example.com>")

    def test_references_are_normalized_deduplicated_bounded_and_source_final(self):
        historic = [
            "<root@example.com>",
            "<root@example.com>",
            "invalid\r\nBcc: injected@example.com",
            "<source@example.com>",
            *[f"<ancestor-{index}@example.com>" for index in range(50)],
        ]
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            source_result=_successful_source(
                references=historic,
                inReplyTo="<ignored-parent@example.com>",
            ),
        )
        status, _ = harness.run()
        self.assertEqual(status, 200)
        message = harness.smtp_mock.call_args.args[5]
        references = str(message["References"])
        tokens = references.split()
        self.assertLessEqual(len(tokens), 32)
        self.assertLessEqual(len(references), 4096)
        self.assertEqual(tokens[-1], "<source@example.com>")
        self.assertEqual(tokens.count("<source@example.com>"), 1)
        self.assertEqual(tokens.count("<root@example.com>"), 1)
        self.assertIn("<ancestor-49@example.com>", tokens)
        self.assertNotIn("<ancestor-0@example.com>", tokens)
        self.assertNotIn("injected", references)
        self.assertNotIn("<ignored-parent@example.com>", tokens)

    def test_reference_character_bound_keeps_source_once_and_final(self):
        long_references = [
            f"<{'x' * 900}{index}@example.com>" for index in range(8)
        ]
        result = _successful_source(references=long_references)
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            source_result=result,
        )
        status, _ = harness.run()
        self.assertEqual(status, 200)
        references = str(harness.smtp_mock.call_args.args[5]["References"])
        self.assertLessEqual(len(references), 4096)
        self.assertTrue(references.endswith("<source@example.com>"))
        self.assertEqual(references.count("<source@example.com>"), 1)
        self.assertIn(long_references[0], references)
        self.assertIn(long_references[-1], references)
        self.assertNotIn(long_references[1], references)

    def test_in_reply_to_is_only_fallback_when_references_are_absent(self):
        fallback = send_gmail._build_custom_reply_references(
            [],
            "<parent@example.com>",
            "<source@example.com>",
        )
        self.assertEqual(
            fallback,
            "<parent@example.com> <source@example.com>",
        )
        no_fallback_when_present = send_gmail._build_custom_reply_references(
            ["<root@example.com>"],
            "<parent@example.com>",
            "<source@example.com>",
        )
        self.assertEqual(
            no_fallback_when_present,
            "<root@example.com> <source@example.com>",
        )
        no_fallback_for_malformed_parent = send_gmail._build_custom_reply_references(
            [],
            "<parent@example.com> <ambiguous@example.com>",
            "<source@example.com>",
        )
        self.assertEqual(no_fallback_for_malformed_parent, "<source@example.com>")

    def test_invalid_context_is_rejected_before_ownership_or_network(self):
        harness = _RequestHarness(
            _base_payload(
                imapReplyContext={**VALID_CONTEXT, "sourceImapUid": "01"}
            )
        )
        status, response = harness.run()
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "invalid_imap_reply_context")
        harness.owned_mock.assert_not_called()
        harness.resolved_mock.assert_not_called()
        harness.open_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_reader_failures_map_before_smtp_and_connection_logs_out(self):
        cases = {
            "invalid_folder": (400, "invalid_imap_reply_context"),
            "invalid_imap_uid": (400, "invalid_imap_reply_context"),
            "invalid_uid_validity": (400, "invalid_imap_reply_context"),
            "uid_validity_changed": (409, "imap_reply_source_stale"),
            "message_not_found": (409, "imap_reply_source_stale"),
            "folder_unavailable": (409, "imap_reply_source_stale"),
            "imap_reply_source_unthreadable": (
                422,
                "imap_reply_source_unthreadable",
            ),
            "uid_validity_unavailable": (503, "imap_reply_source_unavailable"),
            "message_identity_unconfirmed": (
                503,
                "imap_reply_source_unavailable",
            ),
            "provider_unavailable": (503, "imap_reply_source_unavailable"),
            "unexpected_provider_failure": (
                503,
                "imap_reply_source_unavailable",
            ),
        }
        for provider_code, expected in cases.items():
            with self.subTest(provider_code=provider_code):
                harness = _RequestHarness(
                    _base_payload(imapReplyContext=VALID_CONTEXT),
                    source_result=_failed_source(provider_code),
                )
                status, response = harness.run()
                self.assertEqual(
                    (status, response["error"]["code"]),
                    expected,
                )
                harness.smtp_mock.assert_not_called()
                harness.connection.close.assert_not_called()
                harness.connection.logout.assert_called_once_with()

    def test_missing_malformed_or_ambiguous_source_message_id_is_unthreadable(self):
        malformed_ids = (
            None,
            "source@example.com",
            "<bad value@example.com>",
            "<one@example.com> <two@example.com>",
            "<source@example.com>\r\nBcc: injected@example.com",
            '<"a>b"@example.com> <two@example.com>',
            '<"a>b"@example.com>\r\nBcc: injected@example.com',
            "<source@[route\\@host]>",
            "<source@[route\\>host]>",
            "<source@[route\\]host]>",
            ["<source@example.com>"],
        )
        for message_id in malformed_ids:
            with self.subTest(message_id=message_id):
                harness = _RequestHarness(
                    _base_payload(imapReplyContext=VALID_CONTEXT),
                    source_result=_successful_source(messageId=message_id),
                )
                status, response = harness.run()
                self.assertEqual(
                    (status, response["error"]["code"]),
                    (422, "imap_reply_source_unthreadable"),
                )
                harness.smtp_mock.assert_not_called()

    def test_custom_reply_accepts_normalized_quoted_and_domain_literal_ids(self):
        for message_id in (
            '<"quoted local"@example.com>',
            '<"a>b"@example.com>',
            '<""@example.com>',
            "<source@[IPv6:2001:db8::1]>",
            "<source@[route>segment@host]>",
        ):
            with self.subTest(message_id=message_id):
                harness = _RequestHarness(
                    _base_payload(imapReplyContext=VALID_CONTEXT),
                    source_result=_successful_source(
                        messageId=message_id,
                        references=[],
                    ),
                )
                status, response = harness.run()

                self.assertEqual((status, response), (200, {"ok": True}))
                message = harness.smtp_mock.call_args.args[5]
                self.assertEqual(str(message["In-Reply-To"]), message_id)
                self.assertEqual(str(message["References"]), message_id)

    def test_mismatched_reader_identity_is_unavailable_and_never_sent(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            source_result=_successful_source(imapUid="43"),
        )
        status, response = harness.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (503, "imap_reply_source_unavailable"),
        )
        harness.smtp_mock.assert_not_called()

    def test_connection_and_read_exceptions_are_unavailable_before_smtp(self):
        connection_failure = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            open_side_effect=OSError("offline"),
        )
        status, response = connection_failure.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (503, "imap_reply_source_unavailable"),
        )
        connection_failure.source_mock.assert_not_called()
        connection_failure.smtp_mock.assert_not_called()

        read_failure = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            read_side_effect=TimeoutError("timeout"),
        )
        status, response = read_failure.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (503, "imap_reply_source_unavailable"),
        )
        read_failure.smtp_mock.assert_not_called()
        read_failure.connection.close.assert_not_called()
        read_failure.connection.logout.assert_called_once_with()

    def test_imap_authentication_rejection_requires_reconnect_before_smtp(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            open_side_effect=send_gmail._CustomImapAuthenticationError(),
        )
        status, response = harness.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (401, "reconnect_required"),
        )
        harness.source_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()

    def test_smtp_is_attempted_exactly_once_after_successful_source_read(self):
        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            smtp_side_effect=_smtp_error("smtp_send_failed"),
        )
        status, response = harness.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (502, "send_failed"),
        )
        harness.source_mock.assert_called_once()
        harness.smtp_mock.assert_called_once()

    def test_connection_uses_stored_ssl_shape_and_logs_in_once(self):
        ssl_connection = Mock()
        with patch.object(
            send_gmail.imaplib,
            "IMAP4_SSL",
            return_value=ssl_connection,
        ) as ssl_constructor, patch.object(
            send_gmail.imaplib,
            "IMAP4",
        ) as plain_constructor:
            connection = send_gmail._open_custom_imap_connection(CUSTOM_MAILBOX)
        self.assertIs(connection, ssl_connection)
        ssl_constructor.assert_called_once_with(
            "imap.example.com",
            993,
            timeout=30,
        )
        plain_constructor.assert_not_called()
        ssl_connection.login.assert_called_once_with(
            "owner@example.com",
            "stored-imap-secret",
        )

    def test_non_ssl_configuration_is_rejected_without_plaintext_login(self):
        mailbox = {
            **CUSTOM_MAILBOX,
            "imap": {
                **CUSTOM_MAILBOX["imap"],
                "port": "143",
                "ssl": False,
            },
        }
        with patch.object(send_gmail.imaplib, "IMAP4_SSL") as ssl_constructor:
            with self.assertRaises(ValueError):
                send_gmail._open_custom_imap_connection(mailbox)
        ssl_constructor.assert_not_called()

        for invalid_port in (True, "0143", "0", "65536"):
            with self.subTest(invalid_port=invalid_port):
                invalid_mailbox = {
                    **CUSTOM_MAILBOX,
                    "imap": {
                        **CUSTOM_MAILBOX["imap"],
                        "port": invalid_port,
                    },
                }
                with self.assertRaises(ValueError):
                    send_gmail._custom_imap_connection_config(invalid_mailbox)

    def test_login_rejection_is_distinguished_without_imap_close_mutation(self):
        rejected_connection = Mock()
        rejected_connection.login.side_effect = imaplib.IMAP4.error(
            "authentication failed"
        )
        with patch.object(
            send_gmail.imaplib,
            "IMAP4_SSL",
            return_value=rejected_connection,
        ):
            with self.assertRaises(send_gmail._CustomImapAuthenticationError):
                send_gmail._open_custom_imap_connection(CUSTOM_MAILBOX)
        rejected_connection.close.assert_not_called()
        rejected_connection.logout.assert_called_once_with()

    def test_login_transport_abort_is_unavailable_not_reconnect_required(self):
        aborted_connection = Mock()
        aborted_connection.login.side_effect = imaplib.IMAP4.abort(
            "transport aborted"
        )
        with patch.object(
            send_gmail.imaplib,
            "IMAP4_SSL",
            return_value=aborted_connection,
        ):
            with self.assertRaises(imaplib.IMAP4.abort):
                send_gmail._open_custom_imap_connection(CUSTOM_MAILBOX)
        aborted_connection.close.assert_not_called()
        aborted_connection.logout.assert_called_once_with()

        harness = _RequestHarness(
            _base_payload(imapReplyContext=VALID_CONTEXT),
            open_side_effect=imaplib.IMAP4.abort("transport aborted"),
        )
        status, response = harness.run()
        self.assertEqual(
            (status, response["error"]["code"]),
            (503, "imap_reply_source_unavailable"),
        )
        harness.source_mock.assert_not_called()
        harness.smtp_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
