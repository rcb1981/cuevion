from __future__ import annotations

import base64
import unittest
from datetime import datetime, timezone
from email import message_from_bytes
from urllib.parse import parse_qs, urlsplit

from api.out_of_office_providers import (
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailHttpError,
    GmailOutOfOfficeAdapter,
    ImapOutOfOfficeAdapter,
)
from api.out_of_office_store import (
    build_gmail_out_of_office_cursor,
    build_imap_out_of_office_cursor,
    normalize_out_of_office_settings,
)
from api.out_of_office_worker import OutOfOfficeCursorReset


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
CREDENTIAL_VERSION = base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode("ascii")


def google_mailbox():
    return {
        "id": "gmail-1",
        "provider": "google",
        "email": "label@example.com",
        "connected": True,
        "connectionStatus": "connected",
    }


def google_token(access_token="token-1"):
    return {
        "provider": "google",
        "email": "label@example.com",
        "owner_email": "owner@example.com",
        "access_token": access_token,
        "refresh_token": "refresh-1",
        "token_type": "Bearer",
        "scope": f"{GMAIL_MODIFY_SCOPE} {GMAIL_SEND_SCOPE}",
        "expires_at": None,
        "expires_in": None,
        "_storage_backend": "upstash_redis_rest",
        "_storage_durable": True,
    }


class FakeGmailHttp:
    def __init__(self):
        self.calls = []
        self.raise_401_once = False
        self.history_404 = False
        self.sent_message = None

    def __call__(self, access_token, url, *, method="GET", payload=None):
        self.calls.append((access_token, url, method, payload))
        if self.raise_401_once:
            self.raise_401_once = False
            raise GmailHttpError(401)

        path = urlsplit(url)
        if path.path.endswith("/profile"):
            return {"emailAddress": "label@example.com", "historyId": "100"}

        if path.path.endswith("/history"):
            if self.history_404:
                raise GmailHttpError(404)
            query = parse_qs(path.query)
            assert query["startHistoryId"] == ["100"]
            assert query["historyTypes"] == ["messageAdded"]
            assert query["labelId"] == ["INBOX"]
            return {
                "historyId": "110",
                "history": [
                    {
                        "id": "101",
                        "messagesAdded": [
                            {"message": {"id": "msg-1", "threadId": "thread-1"}}
                        ],
                    }
                ],
            }

        if "/messages/msg-1" in path.path and method == "GET":
            return {
                "id": "msg-1",
                "threadId": "thread-1",
                "internalDate": str(int(NOW.timestamp() * 1000)),
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Artist <artist@example.com>"},
                        {"name": "Subject", "value": "Demo follow-up"},
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {"name": "Auto-Submitted", "value": "no"},
                    ]
                },
            }

        if path.path.endswith("/messages/send") and method == "POST":
            raw = payload["raw"]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            self.sent_message = message_from_bytes(
                base64.urlsafe_b64decode(padded.encode("ascii"))
            )
            return {"id": "sent-1", "threadId": "sent-thread"}

        raise AssertionError((access_token, url, method, payload))


class GmailAdapterTests(unittest.TestCase):
    def _adapter(self, http=None, refresh_calls=None):
        http = http or FakeGmailHttp()
        refresh_calls = refresh_calls if refresh_calls is not None else []

        def load_token(email, *, owner_email):
            self.assertEqual(email, "label@example.com")
            self.assertEqual(owner_email, "owner@example.com")
            return google_token(), None

        def refresh_token(email, *, owner_email):
            refresh_calls.append((email, owner_email))
            return google_token("token-2"), None

        return (
            GmailOutOfOfficeAdapter(
                load_token=load_token,
                refresh_token=refresh_token,
                http_json=http,
            ),
            http,
        )

    def test_baseline_uses_profile_history_id(self):
        adapter, http = self._adapter()
        cursor = adapter.baseline(
            google_mailbox(),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(cursor["provider"], "google")
        self.assertEqual(cursor["historyId"], "100")
        self.assertTrue(http.calls[0][1].endswith("/profile"))

    def test_history_fetch_projects_only_new_inbox_candidate(self):
        adapter, _http = self._adapter()
        batch = adapter.fetch_since(
            google_mailbox(),
            build_gmail_out_of_office_cursor("100", now=NOW),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(batch["cursor"]["historyId"], "110")
        self.assertEqual(len(batch["candidates"]), 1)
        candidate = batch["candidates"][0]
        self.assertEqual(candidate["senderEmail"], "artist@example.com")
        self.assertEqual(candidate["rfcMessageId"], "<source@example.com>")
        self.assertEqual(candidate["receivedAt"], "2026-10-08T10:00:00Z")
        self.assertEqual(candidate["headers"]["auto-submitted"], "no")

    def test_expired_history_rebaselines_without_old_message_projection(self):
        http = FakeGmailHttp()
        http.history_404 = True
        adapter, _ = self._adapter(http=http)
        with self.assertRaises(OutOfOfficeCursorReset) as raised:
            adapter.fetch_since(
                google_mailbox(),
                build_gmail_out_of_office_cursor("100", now=NOW),
                owner_email="owner@example.com",
                now=NOW,
            )
        self.assertEqual(raised.exception.cursor["historyId"], "100")

    def test_401_refreshes_once_and_retries(self):
        http = FakeGmailHttp()
        http.raise_401_once = True
        refresh_calls = []
        adapter, _ = self._adapter(http=http, refresh_calls=refresh_calls)
        cursor = adapter.baseline(
            google_mailbox(),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(cursor["historyId"], "100")
        self.assertEqual(refresh_calls, [("label@example.com", "owner@example.com")])
        self.assertEqual(http.calls[0][0], "token-1")
        self.assertEqual(http.calls[1][0], "token-2")

    def test_send_sets_auto_reply_loop_prevention_headers(self):
        adapter, http = self._adapter()
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": None,
                "subject": "Out of office",
                "message": "Back on Monday.",
            },
            now=NOW,
        )
        adapter.send_reply(
            google_mailbox(),
            {
                "providerMessageId": "msg-1",
                "senderEmail": "artist@example.com",
                "rfcMessageId": "<source@example.com>",
                "receivedAt": "2026-10-08T10:00:00Z",
                "headers": {},
            },
            settings,
            owner_email="owner@example.com",
        )
        message = http.sent_message
        self.assertIsNotNone(message)
        self.assertEqual(message["From"], "label@example.com")
        self.assertEqual(message["To"], "artist@example.com")
        self.assertEqual(message["Subject"], "Out of office")
        self.assertEqual(message["Auto-Submitted"], "auto-replied")
        self.assertEqual(message["X-Auto-Response-Suppress"], "All")
        self.assertEqual(message["In-Reply-To"], "<source@example.com>")
        self.assertIn("Back on Monday.", message.get_payload())


def imap_mailbox():
    return {
        "id": "imap-1",
        "provider": "custom_imap",
        "email": "label@example.com",
        "connected": True,
        "connectionStatus": "connected",
        "imapConnectionStatus": "connected",
        "smtpConnectionStatus": "connected",
        "fullyConnected": True,
        "credentialVersion": CREDENTIAL_VERSION,
        "customImap": {
            "host": "imap.example.com",
            "port": "993",
            "ssl": True,
            "username": "label@example.com",
        },
        "customSmtp": {
            "host": "smtp.example.com",
            "port": "587",
            "security": "starttls",
            "username": "",
            "useSameCredentials": True,
        },
    }


def secret_result():
    return {
        "status": "present",
        "record": {
            "v": 1,
            "mailboxId": "imap-1",
            "updatedAt": "2026-10-08T10:00:00Z",
            "imapPassword": "imap-secret",
            "smtpPassword": "",
            "credentialVersion": CREDENTIAL_VERSION,
        },
        "error": None,
    }


RAW_HEADERS = (
    b"From: Artist <artist@example.com>\r\n"
    b"Subject: New demo\r\n"
    b"Message-ID: <imap-source@example.com>\r\n"
    b"Auto-Submitted: no\r\n"
    b"\r\n"
)


class FakeImap:
    def __init__(self, *, uid_validity="777", uid_next="43", search_uids=""):
        self.uid_validity = uid_validity
        self.uid_next = uid_next
        self.search_uids = search_uids
        self.calls = []
        self.logged_out = False

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def response(self, name):
        self.calls.append(("response", name))
        if name == "UIDVALIDITY":
            return "UIDVALIDITY", [self.uid_validity.encode("ascii")]
        if name == "UIDNEXT":
            return "UIDNEXT", [self.uid_next.encode("ascii")]
        raise AssertionError(name)

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [self.search_uids.encode("ascii")]
        if command == "FETCH":
            uid = args[0]
            metadata = (
                f'1 (UID {uid} INTERNALDATE "08-Oct-2026 10:00:00 +0000" '
                f'BODY[HEADER.FIELDS (FROM SUBJECT AUTO-SUBMITTED PRECEDENCE LIST-ID LIST-UNSUBSCRIBE '
                f'X-AUTO-RESPONSE-SUPPRESS X-AUTOREPLY X-AUTORESPOND RETURN-PATH MESSAGE-ID)] '
                f'{{{len(RAW_HEADERS)}}}'.encode("ascii")
            )
            return "OK", [(metadata, RAW_HEADERS), b")"]
        raise AssertionError((command, args))

    def logout(self):
        self.logged_out = True
        return "BYE", [b"logout"]


class ImapAdapterTests(unittest.TestCase):
    def _adapter(self, fake_imap, sent=None):
        sent = sent if sent is not None else []

        def read_secret(owner_email, mailbox_id):
            self.assertEqual(owner_email, "owner@example.com")
            self.assertEqual(mailbox_id, "imap-1")
            return secret_result()

        def connect(host, port, username, password, ssl_enabled, *, timeout=30):
            self.assertEqual(
                (host, port, username, password, ssl_enabled),
                (
                    "imap.example.com",
                    993,
                    "label@example.com",
                    "imap-secret",
                    True,
                ),
            )
            return fake_imap

        def send_smtp(
            host,
            port,
            security,
            username,
            password,
            message,
            recipients,
            *,
            timeout=30,
        ):
            sent.append(
                {
                    "connection": (host, port, security, username, password),
                    "message": message,
                    "recipients": list(recipients),
                }
            )

        return ImapOutOfOfficeAdapter(
            read_secret=read_secret,
            connect_imap=connect,
            send_smtp=send_smtp,
        ), sent

    def test_baseline_uses_uidnext_minus_one_without_searching_old_mail(self):
        fake = FakeImap(uid_validity="777", uid_next="43")
        adapter, _ = self._adapter(fake)
        cursor = adapter.baseline(
            imap_mailbox(),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(cursor["uidValidity"], "777")
        self.assertEqual(cursor["lastUid"], "42")
        self.assertFalse(any(call[:2] == ("uid", "SEARCH") for call in fake.calls))

    def test_no_new_uid_does_not_issue_reversed_search_range(self):
        fake = FakeImap(uid_validity="777", uid_next="43")
        adapter, _ = self._adapter(fake)
        batch = adapter.fetch_since(
            imap_mailbox(),
            build_imap_out_of_office_cursor("777", "42", now=NOW),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(batch["candidates"], [])
        self.assertEqual(batch["cursor"]["lastUid"], "42")
        self.assertFalse(any(call[:2] == ("uid", "SEARCH") for call in fake.calls))

    def test_fetch_uses_finite_uid_range_and_parses_headers(self):
        fake = FakeImap(uid_validity="777", uid_next="45", search_uids="43 44")
        adapter, _ = self._adapter(fake)
        batch = adapter.fetch_since(
            imap_mailbox(),
            build_imap_out_of_office_cursor("777", "42", now=NOW),
            owner_email="owner@example.com",
            now=NOW,
        )
        self.assertEqual(batch["cursor"]["lastUid"], "44")
        self.assertEqual(len(batch["candidates"]), 2)
        self.assertEqual(batch["candidates"][0]["senderEmail"], "artist@example.com")
        self.assertEqual(batch["candidates"][0]["receivedAt"], "2026-10-08T10:00:00Z")
        search = next(call for call in fake.calls if call[:2] == ("uid", "SEARCH"))
        self.assertEqual(search[-1], "43:44")

    def test_uidvalidity_change_rebaselines_without_fetching(self):
        fake = FakeImap(uid_validity="888", uid_next="60")
        adapter, _ = self._adapter(fake)
        with self.assertRaises(OutOfOfficeCursorReset) as raised:
            adapter.fetch_since(
                imap_mailbox(),
                build_imap_out_of_office_cursor("777", "42", now=NOW),
                owner_email="owner@example.com",
                now=NOW,
            )
        self.assertEqual(raised.exception.cursor["uidValidity"], "888")
        self.assertEqual(raised.exception.cursor["lastUid"], "59")
        self.assertFalse(any(call[:2] == ("uid", "SEARCH") for call in fake.calls))

    def test_send_uses_safe_smtp_runtime_and_loop_headers(self):
        fake = FakeImap()
        adapter, sent = self._adapter(fake)
        settings = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": None,
                "subject": "Away",
                "message": "Back soon.",
            },
            now=NOW,
        )
        adapter.send_reply(
            imap_mailbox(),
            {
                "providerMessageId": "43",
                "senderEmail": "artist@example.com",
                "rfcMessageId": "<imap-source@example.com>",
                "receivedAt": "2026-10-08T10:00:00Z",
                "headers": {},
            },
            settings,
            owner_email="owner@example.com",
        )
        self.assertEqual(len(sent), 1)
        item = sent[0]
        self.assertEqual(
            item["connection"],
            (
                "smtp.example.com",
                587,
                "starttls",
                "label@example.com",
                "imap-secret",
            ),
        )
        self.assertEqual(item["recipients"], ["artist@example.com"])
        self.assertEqual(item["message"]["Auto-Submitted"], "auto-replied")
        self.assertEqual(item["message"]["Subject"], "Away")


if __name__ == "__main__":
    unittest.main()
