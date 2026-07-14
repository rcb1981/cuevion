from __future__ import annotations

import sys
import unittest
import base64
import subprocess
from email.header import Header
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import source_message
from .authorization import resolve_internal_collaboration_context
from .models import (
    build_v2_guest_thread_dto,
    normalize_v2_source_message,
    normalize_v2_thread_record,
)
from . import redis_store
from .source_message import resolve_source_message
from .v2_stateful_test_store import StatefulV2Store


RAW_MESSAGE = (
    b"From: Sender Name <sender@example.com>\r\n"
    b"Subject: Review this\r\n"
    b"Date: Tue, 14 Jul 2026 10:00:00 +0200\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Plain source body\r\n"
)


def authorization(provider: str):
    def resolver(_headers, mailbox_id, *, required_action):
        if required_action != "create":
            raise AssertionError("unexpected action")
        return resolve_internal_collaboration_context(
            [], mailbox_id, required_action="create",
            user_resolver=lambda _raw: ({"email": "owner@example.com", "name": "Owner"}, None),
            mailbox_resolver=lambda _raw, resolved_id: {
                "status": "ok", "user": {"email": "owner@example.com"},
                "inbox": {"id": resolved_id, "provider": provider},
            },
        )
    return resolver


class CollaborationV2SourceMessageTests(unittest.TestCase):
    def _assert_confidential_mime(
        self,
        raw_message: bytes,
        *,
        hidden: tuple[str, ...],
        expected_body: str,
    ) -> None:
        result = resolve_source_message(
            {},
            {
                "mailboxId": "mailbox-1",
                "sourceRef": {"providerMessageId": "gmail-confidentiality"},
            },
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {
                "status": "ok",
                "rawMessage": raw_message,
            },
        )
        self.assertEqual(result["status"], "ok")
        extracted = result["source"]["sourceMessage"]
        self.assertEqual(extracted["bodyText"], expected_body)

        normalized_source = normalize_v2_source_message(extracted)
        self.assertIsNotNone(normalized_source)
        normalized_thread = normalize_v2_thread_record({
            "v": 2,
            "collaborationId": "A" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "owner@example.com",
            "mailboxId": "mailbox-1",
            "sourceRef": result["source"]["sourceRef"],
            "sourceMessage": normalized_source,
            "state": "needs_review",
            "messages": [],
            "createdAt": 1_800_000_000_100,
            "updatedAt": 1_800_000_000_100,
        })
        self.assertIsNotNone(normalized_thread)
        store = StatefulV2Store()
        with patch.object(
            redis_store,
            "resolve_v2_index_hmac_keys",
            return_value=(b"k" * 32, None),
        ):
            created = redis_store._create_v2_thread(
                normalized_thread, command_transport=store
            )
        self.assertEqual(created["status"], "ok")
        self.assertTrue(created["created"])
        loaded = redis_store._load_v2_thread(
            normalized_thread["collaborationId"], command_transport=store
        )
        self.assertEqual(loaded["status"], "ok")
        stored_thread = loaded["record"]
        self.assertEqual(stored_thread, normalized_thread)
        guest_dto = build_v2_guest_thread_dto(stored_thread)
        self.assertIsNotNone(guest_dto)

        layers = {
            "provider/source result": result,
            "extracted sourceMessage": extracted,
            "normalized sourceMessage": normalized_source,
            "stored Collaboration wire values": store.values,
            "stored Collaboration thread": stored_thread,
            "guest-safe DTO": guest_dto,
        }
        for secret in hidden:
            for layer_name, layer in layers.items():
                with self.subTest(secret=secret, layer=layer_name):
                    self.assertNotIn(secret, repr(layer))

    def test_google_success_derives_provider_and_returns_plain_snapshot(self):
        calls = []
        result = resolve_source_message(
            {},
            {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda headers, mailbox_id, source_ref: (
                calls.append((headers, mailbox_id, source_ref))
                or {"status": "ok", "rawMessage": RAW_MESSAGE}
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["source"]["sourceRef"],
            {"provider": "google", "providerMessageId": "gmail-1"},
        )
        self.assertEqual(result["source"]["sourceMessage"]["bodyText"], "Plain source body")
        self.assertEqual(calls[0][1], "mailbox-1")

    def test_imap_success_requires_exact_uidvalidity_match(self):
        # Existing inbox DTOs carry these exact provider identifiers as decimal
        # strings; the v2 record preserves their bounded canonical string form.
        locator = {"folder": "INBOX", "uidValidity": "77", "imapUid": "9"}
        result = resolve_source_message(
            {}, {"mailboxId": "mailbox-2", "sourceRef": locator},
            authorization_resolver=authorization("custom_imap"),
            imap_fetcher=lambda _headers, _mailbox, _source: {
                "status": "ok", "rawMessage": RAW_MESSAGE, "uidValidity": "77",
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"]["sourceRef"]["provider"], "custom_imap")
        changed = resolve_source_message(
            {}, {"mailboxId": "mailbox-2", "sourceRef": locator},
            authorization_resolver=authorization("custom_imap"),
            imap_fetcher=lambda _headers, _mailbox, _source: {
                "status": "ok", "rawMessage": RAW_MESSAGE, "uidValidity": "78",
            },
        )
        self.assertEqual(changed["error"]["code"], "source_changed")

    def test_uidvalidity_is_strict_ascii_canonical_decimal(self):
        class Mailbox:
            def __init__(self, value):
                self.value = value

            def response(self, name):
                self.asserted_name = name
                return "UIDVALIDITY", [self.value]

        for value in (b"77", "77", b"99999999999999999999"):
            with self.subTest(valid=value):
                mailbox = Mailbox(value)
                expected = value.decode("ascii") if isinstance(value, bytes) else value
                self.assertEqual(source_message._imap_uid_validity(mailbox), expected)
                self.assertEqual(mailbox.asserted_name, "UIDVALIDITY")

        invalid_values = (
            b"\xff77",
            b"77\xff",
            "\ufffd77",
            b"77\x00",
            b"7\n7",
            b" 77",
            b"77 ",
            b"\t77",
            b"0",
            b"01",
            b"2.0",
            b"2e0",
            b"-1",
            b"",
            b"100000000000000000000",
            77,
            bytearray(b"77"),
        )
        for value in invalid_values:
            with self.subTest(invalid=value):
                self.assertIsNone(source_message._imap_uid_validity(Mailbox(value)))
        wrong_tag = Mailbox(b"77")
        wrong_tag.response = lambda _name: ("OK", [b"77"])
        self.assertIsNone(source_message._imap_uid_validity(wrong_tag))

    def test_provider_decimal_identifiers_reject_noncanonical_text_before_io(self):
        invalid_identifiers = (
            "\ufffd77",
            "77\x00",
            "7\n7",
            " 77",
            "77 ",
            "0",
            "01",
            "2.0",
            "2e0",
            "-1",
            "100000000000000000000",
        )
        for field in ("uidValidity", "imapUid"):
            for identifier in invalid_identifiers:
                locator = {
                    "folder": "INBOX",
                    "uidValidity": "77",
                    "imapUid": "9",
                }
                locator[field] = identifier
                calls = []
                with self.subTest(field=field, identifier=identifier):
                    result = resolve_source_message(
                        {},
                        {"mailboxId": "mailbox-2", "sourceRef": locator},
                        authorization_resolver=lambda *_args, **_kwargs: calls.append(
                            "authorization"
                        ),
                        imap_fetcher=lambda *_args: calls.append("provider"),
                    )
                    self.assertEqual(result["error"]["code"], "invalid_request")
                    self.assertEqual(calls, [])

        for identifier in (
            b"gmail-1",
            "\ufffdgmail-1",
            "gma\x00il-1",
            "gmail\n1",
            " gmail-1",
            "gmail-1 ",
            "gmail-\u00e9",
        ):
            calls = []
            with self.subTest(provider_message_id=identifier):
                result = resolve_source_message(
                    {},
                    {
                        "mailboxId": "mailbox-1",
                        "sourceRef": {"providerMessageId": identifier},
                    },
                    authorization_resolver=lambda *_args, **_kwargs: calls.append(
                        "authorization"
                    ),
                    google_fetcher=lambda *_args: calls.append("provider"),
                )
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(calls, [])

    def test_rejects_browser_provider_snapshots_secrets_and_non_inbox_folder(self):
        forbidden_payloads = [
            {
                "mailboxId": "mailbox-1",
                "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
            },
            {
                "mailboxId": "mailbox-1",
                "sourceRef": {"providerMessageId": "gmail-1"},
                "sourceMessage": {"bodyText": "browser supplied"},
            },
            {
                "mailboxId": "mailbox-1",
                "sourceRef": {"providerMessageId": "gmail-1", "accessToken": "secret"},
            },
            {
                "mailboxId": "mailbox-2",
                "sourceRef": {"folder": "Archive", "uidValidity": 1, "imapUid": 2},
            },
        ]
        for payload in forbidden_payloads:
            provider = "custom_imap" if payload["mailboxId"] == "mailbox-2" else "google"
            result = resolve_source_message(
                {}, payload, authorization_resolver=authorization(provider),
                google_fetcher=lambda *_args: self.fail("provider boundary must not run"),
                imap_fetcher=lambda *_args: self.fail("provider boundary must not run"),
            )
            self.assertEqual(result["error"]["code"], "invalid_request")

    def test_prevalidation_runs_before_authorization_or_provider_access(self):
        calls = []
        result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "bad\nvalue"}},
            authorization_resolver=lambda *_args, **_kwargs: calls.append("authorization"),
            google_fetcher=lambda *_args: calls.append("provider"),
        )
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(calls, [])

    def test_default_imap_adapter_uses_one_bounded_body_peek(self):
        class Mailbox:
            def __init__(self):
                self.calls = []
            def select(self, folder, readonly):
                self.calls.append(("select", folder, readonly))
                return "OK", []
            def response(self, name):
                return "UIDVALIDITY", [b"77"]
            def uid(self, *args):
                self.calls.append(("uid", *args))
                return "OK", [(b"meta", RAW_MESSAGE)]
            def logout(self):
                self.calls.append(("logout",))
        mailbox = Mailbox()
        authenticated_imap = SimpleNamespace(
            __name__="api.inboxes.authenticated_imap",
            resolve_authenticated_imap_mailbox=lambda *_args: {
                "status": "ok",
                "mailbox": {
                    "imap": {
                        "host": "h", "port": 993, "username": "u",
                        "password": "p", "ssl": True,
                    }
                },
            }
        )
        imap_connect_preview = SimpleNamespace(
            __name__="imap_connect_preview",
            connect_mailbox_with_settings=lambda *_args: mailbox
        )
        with patch.dict(
            sys.modules,
            {
                "api.inboxes.authenticated_imap": authenticated_imap,
                "authenticated_imap": authenticated_imap,
                "imap_connect_preview": imap_connect_preview,
            },
        ):
            result = source_message._default_imap_fetcher(
                {}, "mailbox-2", {"provider": "custom_imap", "folder": "INBOX", "uidValidity": "77", "imapUid": "9"}
            )
        self.assertEqual(result["status"], "ok")
        uid_call = next(call for call in mailbox.calls if call[0] == "uid")
        self.assertEqual(uid_call, ("uid", "fetch", "9", f"(UID BODY.PEEK[]<0.{source_message.MAX_SOURCE_MESSAGE_BYTES + 1}>)"))
        self.assertEqual(sum(1 for call in mailbox.calls if call[0] == "uid"), 1)

    def test_default_google_adapter_uses_owned_context_and_strict_payload(self):
        encoded = base64.urlsafe_b64encode(RAW_MESSAGE).decode("ascii").rstrip("=")
        request_calls = []
        fetch_module = SimpleNamespace(
            _request_with_one_refresh=lambda context, path: (
                request_calls.append((context, path)) or ({"raw": encoded}, None, context, None)
            )
        )
        with patch("api.inboxes.authenticated_gmail.resolve_authenticated_gmail", return_value={"status": "ok", "context": {"owner_email": "owner@example.com", "refresh_attempted": False}}), patch.object(source_message, "_load_fetch_gmail_module", return_value=fetch_module):
            result = source_message._default_google_fetcher({}, "mailbox-1", {"provider": "google", "providerMessageId": "gmail-1"})
        self.assertEqual(result, {"status": "ok", "rawMessage": RAW_MESSAGE})
        self.assertEqual(request_calls[0][1], "/messages/gmail-1?format=raw")
        fetch_module._request_with_one_refresh = lambda context, path: ({"raw": encoded, "unexpected": "secret"}, None, context, None)
        with patch("api.inboxes.authenticated_gmail.resolve_authenticated_gmail", return_value={"status": "ok", "context": {"owner_email": "owner@example.com", "refresh_attempted": False}}), patch.object(source_message, "_load_fetch_gmail_module", return_value=fetch_module):
            rejected = source_message._default_google_fetcher({}, "mailbox-1", {"provider": "google", "providerMessageId": "gmail-1"})
        self.assertEqual(rejected["error"]["code"], "provider_unavailable")

    def test_google_helper_load_has_only_canonical_provider_identities(self):
        script = """
import sys

assert "api.inboxes.authenticated_gmail" not in sys.modules
assert "api.inboxes.fetch-gmail" not in sys.modules
assert "authenticated_gmail" not in sys.modules
from api.collaboration import source_message
assert "api.inboxes.authenticated_gmail" not in sys.modules
assert "api.inboxes.fetch-gmail" not in sys.modules
assert "authenticated_gmail" not in sys.modules
before_path = list(sys.path)
helper = source_message._load_fetch_gmail_module()
canonical_auth = sys.modules["api.inboxes.authenticated_gmail"]
assert helper.__name__ == "api.inboxes.fetch-gmail"
assert helper.resolve_authenticated_gmail is canonical_auth.resolve_authenticated_gmail
assert "api.inboxes.fetch-gmail" in sys.modules
assert sys.modules["authenticated_gmail"] is canonical_auth
assert sys.modules["fetch-gmail"] is helper
assert "cuevion_collaboration_fetch_gmail" not in sys.modules
assert sys.path == before_path
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
        )

    def test_provider_boundaries_reject_preloaded_legacy_identities(self):
        for legacy_name, invoke in (
            (
                "authenticated_gmail",
                source_message._load_fetch_gmail_module,
            ),
            (
                "authenticated_imap",
                lambda: source_message._default_imap_fetcher(
                    {},
                    "mailbox-2",
                    {
                        "provider": "custom_imap",
                        "folder": "INBOX",
                        "uidValidity": "77",
                        "imapUid": "9",
                    },
                ),
            ),
        ):
            with self.subTest(legacy_name=legacy_name), patch.dict(
                sys.modules, {legacy_name: SimpleNamespace()}
            ), patch.object(source_message.importlib, "import_module") as importer:
                if legacy_name == "authenticated_gmail":
                    with self.assertRaises(RuntimeError):
                        invoke()
                else:
                    self.assertEqual(
                        invoke()["error"]["code"], "provider_unavailable"
                    )
                importer.assert_not_called()

    def test_snapshot_header_limits_are_utf8_bytes_and_replacement_is_rejected(self):
        def raw_with_subject(subject_header: bytes) -> bytes:
            return (
                b"From: Sender <sender@example.com>\r\nSubject: "
                + subject_header
                + b"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nbody"
            )

        within_limit = Header("\u00e9" * 499, "utf-8").encode(
            linesep="\r\n"
        ).encode("ascii")
        over_limit = Header("\u00e9" * 500, "utf-8").encode(
            linesep="\r\n"
        ).encode("ascii")
        within_snapshot = source_message._snapshot_from_raw(
            raw_with_subject(within_limit)
        )
        self.assertIsNotNone(within_snapshot)
        self.assertEqual(within_snapshot["subject"], "\u00e9" * 499)
        self.assertIsNone(source_message._snapshot_from_raw(raw_with_subject(over_limit)))
        self.assertIsNone(
            source_message._snapshot_from_raw(raw_with_subject(b"invalid-\xff-header"))
        )

    def test_authorization_failure_prevents_provider_invocation(self):
        calls = []
        result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=lambda *_args, **_kwargs: {
                "status": "forbidden", "context": None, "error": {"code": "forbidden"},
            },
            google_fetcher=lambda *_args: calls.append("provider"),
        )
        self.assertEqual(result["error"]["code"], "forbidden")
        self.assertEqual(calls, [])

    def test_provider_failures_and_oversized_messages_are_sanitized(self):
        result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {
                "status": "unavailable",
                "error": {"code": "raw-oauth-token-and-provider-stack"},
            },
        )
        self.assertEqual(result["error"], {"code": "provider_unavailable"})
        oversized = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": b"x" * 2_097_153},
        )
        self.assertEqual(oversized["error"]["code"], "provider_unavailable")

    def test_html_and_attachments_never_enter_source_snapshot(self):
        raw = (
            b"From: Sender <sender@example.com>\r\nSubject: HTML only\r\n"
            b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
            b"--x\r\nContent-Type: text/html\r\n\r\n<b>hidden html</b>\r\n"
            b"--x\r\nContent-Type: text/plain\r\nContent-Disposition: attachment\r\n\r\nsecret attachment\r\n--x--"
        )
        result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-1"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": raw},
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"]["sourceMessage"]["bodyText"], "")
        self.assertNotIn("hidden html", repr(result))
        self.assertNotIn("secret attachment", repr(result))

        nested = (
            b"From: Sender <sender@example.com>\r\nSubject: Nested MIME\r\n"
            b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
            b"--outer\r\nContent-Type: multipart/alternative; boundary=visible\r\n\r\n"
            b"--visible\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nVisible body\r\n"
            b"--visible\r\nContent-Type: text/html\r\n\r\n<b>hidden alternative html</b>\r\n--visible--\r\n"
            b"--outer\r\nContent-Type: message/rfc822\r\n\r\n"
            b"From: Secret <secret@example.com>\r\nContent-Type: multipart/alternative; boundary=forwarded\r\n\r\n"
            b"--forwarded\r\nContent-Type: text/plain\r\n\r\nsecret forwarded body\r\n"
            b"--forwarded\r\nContent-Type: text/html\r\n\r\nsecret forwarded html\r\n--forwarded--\r\n"
            b"--outer\r\nContent-Type: multipart/mixed; boundary=attached\r\n"
            b"Content-Disposition: attachment\r\n\r\n"
            b"--attached\r\nContent-Type: multipart/alternative; boundary=deep\r\n\r\n"
            b"--deep\r\nContent-Type: text/plain\r\n\r\nsecret attached subtree\r\n--deep--\r\n--attached--\r\n"
            b"--outer\r\nContent-Type: multipart/related; boundary=filename-parent; name=secret-package.eml\r\n\r\n"
            b"--filename-parent\r\nContent-Type: text/plain\r\n\r\nsecret filename parent\r\n--filename-parent--\r\n"
            b"--outer\r\nContent-Type: text/plain; name=secret-name.txt\r\n\r\nsecret content-type name\r\n"
            b"--outer\r\nContent-Type: text/plain\r\n"
            b"Content-Disposition: inline; filename=secret-inline.txt\r\n\r\nsecret filename body\r\n"
            b"--outer--\r\n"
        )
        nested_result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-2"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": nested},
        )
        self.assertEqual(nested_result["status"], "ok")
        self.assertEqual(nested_result["source"]["sourceMessage"]["bodyText"], "Visible body")

        stored = normalize_v2_thread_record({
            "v": 2, "collaborationId": "A" * 22,
            "ownerEmail": "owner@example.com", "workspaceId": "owner@example.com",
            "mailboxId": "mailbox-1", **nested_result["source"],
            "state": "needs_review", "messages": [],
            "createdAt": 1_800_000_000_100, "updatedAt": 1_800_000_000_100,
        })
        self.assertIsNotNone(stored)
        guest_dto = build_v2_guest_thread_dto(stored)
        self.assertEqual(guest_dto["sharedSource"]["bodyText"], "Visible body")
        for secret in (
            "secret forwarded body", "secret attached subtree",
            "secret forwarded html", "secret filename parent", "secret content-type name",
            "secret filename body", "hidden alternative html",
        ):
            self.assertNotIn(secret, repr(nested_result))
            self.assertNotIn(secret, repr(stored))
            self.assertNotIn(secret, repr(guest_dto))

        inline_related = (
            b"From: Sender <sender@example.com>\r\nSubject: Related\r\n"
            b"Content-Type: multipart/related; boundary=related\r\n\r\n"
            b"--related\r\nContent-Type: text/plain\r\n\r\nRelated visible body\r\n"
            b"--related\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n\r\naW1hZ2U=\r\n"
            b"--related--\r\n"
        )
        related_result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-3"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": inline_related},
        )
        self.assertEqual(related_result["source"]["sourceMessage"]["bodyText"], "Related visible body")

        malformed_nested = (
            b"From: Sender <sender@example.com>\r\nSubject: Malformed\r\n"
            b"Content-Type: multipart/mixed\r\n\r\nsecret malformed subtree"
        )
        malformed_result = resolve_source_message(
            {}, {"mailboxId": "mailbox-1", "sourceRef": {"providerMessageId": "gmail-4"}},
            authorization_resolver=authorization("google"),
            google_fetcher=lambda *_args: {"status": "ok", "rawMessage": malformed_nested},
        )
        self.assertEqual(malformed_result["status"], "ok")
        self.assertNotIn("secret malformed subtree", malformed_result["source"]["sourceMessage"]["bodyText"])

    def test_mime_attachment_classification_is_ancestor_aware_and_fail_closed(self):
        cases = (
            (
                "direct text attachment",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: attachment\r\n\r\n"
                b"hidden-direct-attachment",
                ("hidden-direct-attachment",),
                "",
            ),
            (
                "empty filename parameter",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline; filename=\"\"\r\n\r\n"
                b"hidden-empty-filename",
                ("hidden-empty-filename",),
                "",
            ),
            (
                "whitespace filename parameter",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline; filename=\"   \"\r\n\r\n"
                b"hidden-whitespace-filename",
                ("hidden-whitespace-filename",),
                "",
            ),
            (
                "empty Content-Type name parameter",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain; name=\"\"\r\n\r\n"
                b"hidden-empty-type-name",
                ("hidden-empty-type-name",),
                "",
            ),
            (
                "duplicate Content-Disposition",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline\r\n"
                b"Content-Disposition: inline\r\n\r\n"
                b"hidden-duplicate-disposition",
                ("hidden-duplicate-disposition",),
                "",
            ),
            (
                "duplicate Content-Type",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"hidden-duplicate-type",
                ("hidden-duplicate-type",),
                "",
            ),
            (
                "malformed disposition parameter",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline; filename==secret.txt\r\n\r\n"
                b"hidden-malformed-disposition",
                ("hidden-malformed-disposition",),
                "",
            ),
            (
                "malformed type parameter",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain; name==secret.txt\r\n\r\n"
                b"hidden-malformed-type",
                ("hidden-malformed-type",),
                "",
            ),
            (
                "dangling disposition parameter separator",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline;\r\n\r\n"
                b"hidden-dangling-disposition",
                ("hidden-dangling-disposition",),
                "",
            ),
            (
                "dangling type parameter separator",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain;\r\n\r\n"
                b"hidden-dangling-type",
                ("hidden-dangling-type",),
                "",
            ),
            (
                "dangling disposition separator with CFWS comment",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline; (parser-discarded)\r\n\r\n"
                b"hidden-comment-disposition",
                ("hidden-comment-disposition",),
                "",
            ),
            (
                "dangling type separator with folded CFWS",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain;\r\n\t(parser-discarded)\r\n\r\n"
                b"hidden-folded-type",
                ("hidden-folded-type",),
                "",
            ),
            (
                "message/rfc822 subtree",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: message/rfc822\r\n\r\n"
                b"From: Hidden <hidden@example.com>\r\n"
                b"Content-Type: text/plain\r\n\r\n"
                b"hidden-forwarded-message",
                ("hidden-forwarded-message",),
                "",
            ),
            (
                "nested multipart attachment",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
                b"--outer\r\n"
                b"Content-Type: multipart/mixed; boundary=attached\r\n"
                b"Content-Disposition: attachment\r\n\r\n"
                b"--attached\r\nContent-Type: text/plain\r\n\r\n"
                b"hidden-nested-multipart\r\n"
                b"--attached--\r\n--outer--\r\n",
                ("hidden-nested-multipart",),
                "",
            ),
            (
                "deeply nested attachment ancestry",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
                b"--outer\r\n"
                b"Content-Type: multipart/mixed; boundary=attached\r\n"
                b"Content-Disposition: inline; filename=\"\"\r\n\r\n"
                b"--attached\r\nContent-Type: multipart/alternative; boundary=deep1\r\n\r\n"
                b"--deep1\r\nContent-Type: multipart/related; boundary=deep2\r\n\r\n"
                b"--deep2\r\nContent-Type: text/plain\r\n\r\n"
                b"hidden-deep-ancestry\r\n"
                b"--deep2--\r\n--deep1--\r\n--attached--\r\n--outer--\r\n",
                ("hidden-deep-ancestry",),
                "",
            ),
            (
                "child text part has no attachment metadata",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
                b"--outer\r\n"
                b"Content-Type: multipart/mixed; boundary=attached\r\n"
                b"Content-Disposition: attachment\r\n\r\n"
                b"--attached\r\n\r\nhidden-headerless-child\r\n"
                b"--attached--\r\n--outer--\r\n",
                ("hidden-headerless-child",),
                "",
            ),
            (
                "visible body plus hidden attachment",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
                b"--outer\r\nContent-Type: text/plain\r\n\r\nVisible body\r\n"
                b"--outer\r\nContent-Type: text/plain\r\n"
                b"Content-Disposition: attachment\r\n\r\n"
                b"hidden-beside-visible\r\n--outer--\r\n",
                ("hidden-beside-visible",),
                "Visible body",
            ),
            (
                "legitimate inline multipart alternative",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/alternative; boundary=alternative\r\n\r\n"
                b"--alternative\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
                b"Visible alternative body\r\n"
                b"--alternative\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                b"<p>hidden-alternative-html</p>\r\n--alternative--\r\n",
                ("hidden-alternative-html",),
                "Visible alternative body",
            ),
            (
                "legitimate inline multipart related",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/related; boundary=related\r\n\r\n"
                b"--related\r\nContent-Type: text/plain\r\n\r\n"
                b"Visible related body\r\n"
                b"--related\r\nContent-Type: text/html\r\n\r\n"
                b"<p>hidden-related-html</p>\r\n--related--\r\n",
                ("hidden-related-html",),
                "Visible related body",
            ),
            (
                "legitimate quoted parameter semicolon",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/alternative; boundary=\"alternative;v1\"\r\n\r\n"
                b"--alternative;v1\r\nContent-Type: text/plain\r\n\r\n"
                b"Visible quoted-boundary body\r\n"
                b"--alternative;v1\r\nContent-Type: text/html\r\n\r\n"
                b"<p>hidden-quoted-boundary-html</p>\r\n--alternative;v1--\r\n",
                ("hidden-quoted-boundary-html",),
                "Visible quoted-boundary body",
            ),
            (
                "malformed nested MIME",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=outer\r\n\r\n"
                b"--outer\r\nContent-Type: text/plain\r\n\r\nVisible before malformed\r\n"
                b"--outer\r\nContent-Type: multipart/alternative\r\n\r\n"
                b"hidden-malformed-nested\r\n--outer--\r\n",
                ("hidden-malformed-nested",),
                "",
            ),
            (
                "malformed attachment boundary reparents hidden text",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: multipart/mixed; boundary=shared\r\n\r\n"
                b"--shared\r\n"
                b"Content-Type: multipart/mixed; boundary=shared\r\n"
                b"Content-Disposition: attachment\r\n\r\n"
                b"--shared\r\nContent-Type: text/plain\r\n\r\n"
                b"hidden-reparented-sibling\r\n--shared--\r\n",
                ("hidden-reparented-sibling",),
                "",
            ),
            (
                "non-ASCII disposition metadata",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Disposition: inline; filename=\xff\r\n\r\n"
                b"hidden-nonascii-disposition",
                ("hidden-nonascii-disposition",),
                "",
            ),
            (
                "non-ASCII type metadata",
                b"From: Sender <sender@example.com>\r\n"
                b"Content-Type: text/plain; name=\xff\r\n\r\n"
                b"hidden-nonascii-type",
                ("hidden-nonascii-type",),
                "",
            ),
        )
        for case_name, raw_message, hidden, expected_body in cases:
            with self.subTest(case=case_name):
                self._assert_confidential_mime(
                    raw_message,
                    hidden=hidden,
                    expected_body=expected_body,
                )


if __name__ == "__main__":
    unittest.main()
