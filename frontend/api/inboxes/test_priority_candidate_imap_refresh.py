from __future__ import annotations

import importlib
import sys
import unittest
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))


connect_imap = importlib.import_module("api.inboxes.connect-imap")
imap_preview = importlib.import_module("imap_connect_preview")


RAW_MESSAGE = (
    b"Message-ID: <message@example.test>\r\n"
    b"References: <root@example.test>\r\n"
    b"Date: Tue, 01 Jul 2025 12:00:00 +0200\r\n"
    b"From: IMAP Sender <sender@imap.test>\r\n"
    b"To: owner@imap.test\r\n"
    b"Subject: IMAP candidate\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<p>Private IMAP body marker</p>"
)


class FakeMailbox:
    def login(self, _username, _password):
        return "OK", []

    def uid(self, command, _charset, query):
        if command.lower() == "search" and query == "ALL":
            return "OK", [b"123"]
        raise AssertionError("unexpected UID command")

    def logout(self):
        return "BYE", []


def preview_for(_message, _index, _email, unread, imap_uid, flagged, **_kwargs):
    return {
        "id": "message@example.test",
        "sender": "IMAP Sender",
        "subject": "IMAP candidate",
        "snippet": "Private IMAP body marker",
        "from": "IMAP Sender <sender@imap.test>",
        "createdAt": "2099-01-01T00:00:00+00:00",
        "body": ["Private IMAP body marker"],
        "attachments": [{"name": "private.txt"}],
        "unread": unread,
        "flagged": flagged,
        "imapUid": imap_uid,
    }


class ImapCandidateCurrentWindowTests(unittest.TestCase):
    def build_response(self, raw_message: bytes = RAW_MESSAGE, uid: str | None = "123"):
        with patch.object(
            imap_preview,
            "open_mailbox_connection",
            return_value=FakeMailbox(),
        ), patch.object(
            imap_preview,
            "fetch_recent_messages",
            return_value={
                "messages": [(message_from_bytes(raw_message), True, uid, True)],
                "warnings": [],
                "error": None,
            },
        ), patch.object(
            imap_preview,
            "to_message_preview",
            side_effect=preview_for,
        ), patch.object(
            imap_preview,
            "read_selected_mailbox_uid_validity",
            return_value="456",
        ):
            return imap_preview.build_connect_preview_response(
                {
                    "mailboxId": "mailbox-1",
                    "provider": "custom_imap",
                    "email": "owner@imap.test",
                    "host": "imap.example.test",
                    "port": 993,
                    "ssl": True,
                    "username": "owner@imap.test",
                    "password": "provider-secret",
                    "limit": 20,
                }
            )

    def test_exact_current_window_source_uses_uidvalidity_uid_and_rfc_thread(self):
        status, response = self.build_response()

        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])
        self.assertEqual(response["uidValidity"], "456")
        self.assertEqual(response["inboxUidSet"], ["123"])
        self.assertEqual(len(response["messages"]), 1)
        source = response["_priorityCandidateSources"][0]
        self.assertEqual(source["providerFolder"], "INBOX")
        self.assertEqual(source["uidValidity"], "456")
        self.assertEqual(source["imapUid"], "123")
        self.assertEqual(source["authorityKind"], "rfc")
        self.assertEqual(source["rfcRootMessageId"], "root@example.test")
        self.assertEqual(source["rfcMessageId"], "message@example.test")
        self.assertEqual(
            source["conversationId"],
            "imap:rfc:mailbox-1:root%40example.test",
        )
        self.assertEqual(source["rfcDate"], "Tue, 01 Jul 2025 12:00:00 +0200")
        self.assertNotIn("sequenceNumber", source)
        for forbidden in ("body", "bodyHtml", "attachments", "raw"):
            self.assertNotIn(forbidden, source)

    def test_missing_true_time_retains_browser_preview_but_not_a_usable_time(self):
        raw_without_date = RAW_MESSAGE.replace(
            b"Date: Tue, 01 Jul 2025 12:00:00 +0200\r\n",
            b"",
        )
        status, response = self.build_response(raw_without_date)

        self.assertEqual(status, 200)
        self.assertEqual(
            response["messages"][0]["createdAt"],
            "2099-01-01T00:00:00+00:00",
        )
        self.assertIsNone(response["_priorityCandidateSources"][0]["rfcDate"])

    def test_missing_canonical_uid_skips_candidate_without_dropping_preview(self):
        status, response = self.build_response(uid=None)

        self.assertEqual(status, 200)
        self.assertEqual(len(response["messages"]), 1)
        self.assertEqual(response["_priorityCandidateSources"], [])

    def test_browser_projection_strips_the_private_candidate_sidecar(self):
        status, response = self.build_response()
        self.assertEqual(status, 200)
        projected = connect_imap._preview_success_payload(response)
        self.assertEqual(
            projected,
            {
                "ok": True,
                "messages": response["messages"],
                "inboxUidSet": ["123"],
                "uidValidity": "456",
            },
        )

    def test_route_response_survives_candidate_population_failure(self):
        status, response = self.build_response()
        self.assertEqual(status, 200)
        sent: list[tuple[int, dict]] = []
        request_handler = SimpleNamespace(
            headers={},
            _send_json=lambda status_code, payload: sent.append(
                (status_code, payload)
            ),
        )
        mailbox = {
            "mailboxId": "mailbox-1",
            "email": "owner@imap.test",
            "imap": {
                "host": "imap.example.test",
                "port": 993,
                "ssl": True,
                "username": "owner@imap.test",
                "password": "provider-secret",
            },
        }
        with patch.object(
            connect_imap,
            "resolve_authenticated_imap_mailbox",
            return_value={
                "status": "ok",
                "mailbox": mailbox,
                "memberAuthority": object(),
                "error": None,
            },
        ), patch.object(
            connect_imap,
            "normalize_imap_host",
            return_value="imap.example.test",
        ), patch.object(
            imap_preview,
            "build_connect_preview_response",
            return_value=(200, response),
        ), patch.object(
            connect_imap,
            "populate_runtime_priority_candidates",
            side_effect=RuntimeError("candidate store offline"),
        ), patch.object(
            connect_imap,
            "read_new_inbound_client_mode",
            return_value="off",
        ):
            connect_imap.handler._handle_refresh(
                request_handler,
                {"mode": "refresh", "mailboxId": "mailbox-1", "limit": 20},
            )

        expected = connect_imap._preview_success_payload(response)
        expected["prioritySemanticNewInboundMode"] = "off"
        self.assertEqual(sent, [(200, expected)])


if __name__ == "__main__":
    unittest.main()
