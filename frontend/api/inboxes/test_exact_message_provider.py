from __future__ import annotations

import base64
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

FRONTEND_DIR = Path(__file__).resolve().parents[2]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes import exact_message_provider as provider

RAW = b"From: Sender <sender@example.test>\r\nTo: Owner <owner@example.test>\r\nSubject: Exact source\r\nDate: Tue, 01 Jul 2025 10:00:00 +0000\r\nMessage-ID: <exact@example.test>\r\n\r\nExact body.\r\n"
GOOGLE_SOURCE = {"provider": "google", "providerMessageId": "message-id"}
IMAP_SOURCE = {"provider": "custom_imap", "folder": "INBOX", "uidValidity": "456", "imapUid": "123"}
GOOGLE_CONTEXT = {"mailbox_id": "mailbox-a", "mailbox_email": "owner@example.test", "access_token": "private-token"}
IMAP_CONTEXT = {"mailboxId": "mailbox-a", "email": "owner@example.test", "imap": {"host": "imap.example.test", "port": 993, "username": "private-user", "password": "private-password", "ssl": True}}


class Response:
    def __init__(self, payload, headers=None):
        self.body = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = headers or {}
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        self.read_limits.append(size)
        return self.body.read(size)


class Mailbox:
    def __init__(self, uid_validity="456", fetched=None):
        self.uid_validity = uid_validity
        self.commands = []
        self.fetched = fetched if fetched is not None else ("OK", [(f"9 (UID 123 FLAGS (\\Flagged) RFC822.SIZE {len(RAW)} BODY[]<0> {{{len(RAW)}}}".encode(), RAW), b")"])

    def select(self, folder, *, readonly):
        self.commands.append(("SELECT", folder, readonly))
        return "OK", [b"1"]

    def response(self, name):
        self.commands.append(("RESPONSE", name))
        return name, [self.uid_validity.encode()]

    def uid(self, operation, uid, items):
        self.commands.append((operation, uid, items))
        return self.fetched

    def logout(self):
        self.commands.append(("LOGOUT",))


class ExactGoogleProviderTests(unittest.TestCase):
    def payload(self, **changes):
        return {"id": "message-id", "threadId": "thread-id", "labelIds": ["INBOX", "UNREAD"], "raw": base64.urlsafe_b64encode(RAW).decode().rstrip("="), **changes}

    def run_fetch(self, payload=None, *, source=None, error=None):
        response = Response(self.payload() if payload is None else payload)
        with patch.object(provider, "urlopen", side_effect=error, return_value=response) as request:
            result = provider.fetch_google_message(GOOGLE_CONTEXT, source or GOOGLE_SOURCE)
        self.assertEqual(request.call_count, 1)
        return result, request, response

    def test_exact_get_one_call_no_search_or_mutation(self):
        result, request, response = self.run_fetch()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(request.call_args.args[0].full_url, "https://gmail.googleapis.com/gmail/v1/users/me/messages/message-id?format=raw")
        self.assertEqual(request.call_args.args[0].method, "GET")
        self.assertEqual(response.read_limits, [provider.MAX_PROVIDER_RESPONSE_BYTES + 1])
        message = result["message"]
        self.assertEqual(message["providerMessageId"], "message-id")
        self.assertEqual(message["providerThreadId"], "thread-id")
        self.assertEqual(message["serverMailboxId"], "mailbox-a")
        self.assertEqual(message["body"], ["Exact body."])
        self.assertTrue(message["unread"])
        self.assertNotIn("classification", json.dumps(message))
        self.assertNotIn("private-token", json.dumps(result))

    def test_response_id_mismatch_fails(self):
        self.assertEqual(self.run_fetch(self.payload(id="wrong"))[0], {"status": "invalid_response"})

    def test_missing_message_safe(self):
        error = HTTPError("private-url", 404, "private-provider-message", {}, None)
        self.assertEqual(self.run_fetch(error=error)[0], {"status": "message_not_found"})

    def test_provider_failure_safe_without_retry(self):
        for error in (URLError("private-token"), HTTPError("private-url", 401, "private", {}, None)):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.run_fetch(error=error)[0], {"status": "service_unavailable"})

    def test_input_needs_no_thread_id(self):
        payload = self.payload()
        del payload["threadId"]
        result, _, _ = self.run_fetch(payload)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("providerThreadId", result["message"])

    def test_id_is_encoded_and_revalidated_exactly(self):
        identifier = "some id/with?delimiters&and#fragment"
        result, request, _ = self.run_fetch(self.payload(id=identifier), source={"provider": "google", "providerMessageId": identifier})
        self.assertEqual(result["status"], "ok")
        self.assertIn("some%20id%2Fwith%3Fdelimiters%26and%23fragment?format=raw", request.call_args.args[0].full_url)

    def test_arbitrary_folder_preserved(self):
        for labels, expected in ((["SENT"], "Sent"), (["TRASH"], "Trash"), (["SPAM"], "Spam"), (["DRAFT"], "Drafts"), ([], "Archive")):
            with self.subTest(labels=labels):
                result, _, _ = self.run_fetch(self.payload(labelIds=labels))
                self.assertEqual(result["message"]["providerFolder"], expected)

    def test_missing_raw_bad_labels_and_invalid_json(self):
        for payload in (self.payload(raw="bad="), self.payload(labelIds=["INBOX", "INBOX"]), self.payload(labelIds=["INBOX\n"]), self.payload(raw=None), b"{not JSON", b'{"id":"wrong","id":"message-id"}'):
            with self.subTest(payload=str(payload)[:30]):
                self.assertEqual(self.run_fetch(payload)[0], {"status": "invalid_response"})

    def test_response_body_bound(self):
        self.assertEqual(self.run_fetch(b" " * (provider.MAX_PROVIDER_RESPONSE_BYTES + 1))[0], {"status": "invalid_response"})

    def test_raw_message_bound(self):
        with patch.object(provider, "MAX_MESSAGE_BYTES", len(RAW) - 1):
            self.assertEqual(self.run_fetch()[0], {"status": "invalid_response"})

    def test_safe_adapter_exception(self):
        with patch.object(provider, "to_message_preview", side_effect=RuntimeError("private-content")):
            self.assertEqual(self.run_fetch()[0], {"status": "service_unavailable"})


class ExactImapProviderTests(unittest.TestCase):
    def run_fetch(self, mailbox=None):
        mailbox = mailbox or Mailbox()
        with patch.object(provider, "connect_mailbox_with_settings", return_value=mailbox) as connect:
            result = provider.fetch_imap_message(IMAP_CONTEXT, IMAP_SOURCE)
        self.assertEqual(connect.call_count, 1)
        return result, mailbox

    def test_exact_uid_complete_body_one_fetch_readonly_no_search(self):
        result, mailbox = self.run_fetch()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(mailbox.commands, [("SELECT", '"INBOX"', True), ("RESPONSE", "UIDVALIDITY"), ("FETCH", "123", provider.IMAP_FETCH_ITEMS), ("LOGOUT",)])
        message = result["message"]
        self.assertEqual((message["providerFolder"], message["uidValidity"], message["imapUid"]), ("INBOX", "456", "123"))
        self.assertTrue(message["unread"])
        self.assertTrue(message["flagged"])
        self.assertTrue(message["threadId"])
        self.assertNotIn("private-password", json.dumps(result))

    def test_uidvalidity_mismatch_stops_before_fetch(self):
        result, mailbox = self.run_fetch(Mailbox(uid_validity="999"))
        self.assertEqual(result, {"status": "source_changed"})
        self.assertEqual([command[0] for command in mailbox.commands], ["SELECT", "RESPONSE", "LOGOUT"])

    def test_invalid_live_uidvalidity_stops(self):
        result, mailbox = self.run_fetch(Mailbox(uid_validity="0456"))
        self.assertEqual(result, {"status": "invalid_response"})
        self.assertNotIn("FETCH", [command[0] for command in mailbox.commands])

    def test_missing_uid(self):
        for values in ([None], []):
            self.assertEqual(self.run_fetch(Mailbox(fetched=("OK", values)))[0], {"status": "message_not_found"})

    def test_uid_mismatch_does_not_substitute_sequence_number(self):
        mailbox = Mailbox()
        metadata, raw = mailbox.fetched[1][0]
        mailbox.fetched[1][0] = (metadata.replace(b"9 (UID 123", b"123 (UID 999"), raw)
        self.assertEqual(self.run_fetch(mailbox)[0], {"status": "invalid_response"})

    def test_literal_size_and_full_message_size_must_match(self):
        for field in (b"RFC822.SIZE", b"BODY[]<0>"):
            with self.subTest(field=field):
                mailbox = Mailbox()
                metadata, raw = mailbox.fetched[1][0]
                original = field + (b" " + str(len(raw)).encode() if field == b"RFC822.SIZE" else b" {" + str(len(raw)).encode())
                altered = field + (b" 999999" if field == b"RFC822.SIZE" else b" {999999")
                mailbox.fetched[1][0] = (metadata.replace(original, altered), raw)
                self.assertEqual(self.run_fetch(mailbox)[0], {"status": "invalid_response"})

    def test_flags_uid_and_size_attributes_each_required_once(self):
        for original, changed in ((b"FLAGS (\\Flagged)", b""), (b"FLAGS (\\Flagged)", b"FLAGS (\\Seen) FLAGS (\\Flagged)"), (b"RFC822.SIZE", b"IGNORED.SIZE"), (b"FLAGS (\\Flagged)", b"FLAGS (bad\r\nflag)"), (b"BODY[]<0>", b"BODY[]<1>")):
            with self.subTest(changed=changed):
                mailbox = Mailbox()
                metadata, raw = mailbox.fetched[1][0]
                mailbox.fetched[1][0] = (metadata.replace(original, changed), raw)
                self.assertEqual(self.run_fetch(mailbox)[0], {"status": "invalid_response"})

    def test_metadata_attribute_order_supported(self):
        mailbox = Mailbox()
        mailbox.fetched[1][0] = (f"9 (RFC822.SIZE {len(RAW)} FLAGS (\\Seen) UID 123 BODY[]<0> {{{len(RAW)}}}".encode(), RAW)
        result, _ = self.run_fetch(mailbox)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["message"]["unread"])

    def test_attributes_after_literal_supported(self):
        for before, after in (("", f" UID 123 FLAGS (\\Seen) RFC822.SIZE {len(RAW)}"), ("UID 123 ", f" RFC822.SIZE {len(RAW)} FLAGS (\\Seen)")):
            with self.subTest(before=before):
                mailbox = Mailbox(fetched=("OK", [(f"9 ({before}BODY[]<0> {{{len(RAW)}}}".encode(), RAW), (after + ")").encode()]))
                result, _ = self.run_fetch(mailbox)
                self.assertEqual(result["status"], "ok")
                self.assertFalse(result["message"]["unread"])

    def test_duplicate_uid_after_literal_rejected(self):
        mailbox = Mailbox()
        mailbox.fetched[1][1] = b" UID 123)"
        self.assertEqual(self.run_fetch(mailbox)[0], {"status": "invalid_response"})

    def test_response_and_provider_bound_fail_closed(self):
        for fetched in (("NO", [b"private-host"]), ("OK", [(b"", b"x" * (provider.MAX_MESSAGE_BYTES + 1)), b")"])):
            with self.subTest(size=len(str(fetched))):
                self.assertEqual(self.run_fetch(Mailbox(fetched=fetched))[0], {"status": "invalid_response"})

    def test_fetch_exception_still_logs_out_and_is_safe(self):
        mailbox = Mailbox()
        with patch.object(mailbox, "uid", side_effect=RuntimeError("private-password")):
            result, _ = self.run_fetch(mailbox)
        self.assertEqual(result, {"status": "service_unavailable"})
        self.assertEqual(mailbox.commands[-1], ("LOGOUT",))

    def test_connect_exception_safe(self):
        with patch.object(provider, "connect_mailbox_with_settings", side_effect=RuntimeError("private-host")):
            self.assertEqual(provider.fetch_imap_message(IMAP_CONTEXT, IMAP_SOURCE), {"status": "service_unavailable"})


class ExactMessagePublicationTests(unittest.TestCase):
    def message(self):
        with patch.object(provider, "urlopen", return_value=Response(ExactGoogleProviderTests().payload())):
            return provider.fetch_google_message(GOOGLE_CONTEXT, GOOGLE_SOURCE)["message"]

    def test_closed_publication_rejects_unexpected_or_sensitive_fields(self):
        message = self.message()
        for key in ("access_token", "credentials", "raw", "internalClassification", "recipientUserId"):
            with self.subTest(key=key):
                self.assertIsNone(provider.normalize_exact_message({**message, key: "private"}, "mailbox-a", GOOGLE_SOURCE))

    def test_exact_identity_revalidated(self):
        message = self.message()
        for changed in ({"serverMailboxId": "wrong"}, {"providerMessageId": "wrong"}, {"providerFolder": "Unknown"}, {"providerFolder": "Trash"}):
            with self.subTest(changed=changed):
                self.assertIsNone(provider.normalize_exact_message({**message, **changed}, "mailbox-a", GOOGLE_SOURCE))

    def test_attachment_schema_closed(self):
        message = self.message()
        for attachment in ({"id": "a", "name": "file", "token": "private"}, {"id": "a", "name": "file", "size": True}, {"name": "file"}):
            with self.subTest(attachment=attachment):
                self.assertIsNone(provider.normalize_exact_message({**message, "attachments": [attachment]}, "mailbox-a", GOOGLE_SOURCE))

    def test_no_sensitive_logging(self):
        with patch.object(provider, "urlopen", side_effect=RuntimeError("private-token")), patch("logging.Logger._log") as log:
            self.assertEqual(provider.fetch_google_message(GOOGLE_CONTEXT, GOOGLE_SOURCE), {"status": "service_unavailable"})
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
