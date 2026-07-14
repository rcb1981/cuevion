from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from email import message_from_string
from pathlib import Path
from unittest.mock import Mock, patch

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from api.inboxes.imap_uid_validity import (
    is_canonical_uid_validity,
    parse_uid_validity,
    parse_uid_validity_response,
    read_selected_mailbox_uid_validity,
)

import imap_connect_preview


def _load_route(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load active route {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACTION_ROUTE = _load_route("message-action.py", "strict_uidvalidity_message_action")
ATTACHMENT_ROUTE = _load_route(
    "download-attachment.py", "strict_uidvalidity_download_attachment"
)


class _FakeHandler:
    def __init__(self, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.headers = {"content-length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.path = "/"

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def response(self):
        return json.loads(self.wfile.getvalue())


def _resolved_mailbox() -> dict:
    return {
        "status": "ok",
        "mailbox": {
            "mailboxId": "demo",
            "ownerEmail": "owner@example.com",
            "email": "demo@example.com",
            "imap": {
                "host": "imap.example.com",
                "port": 993,
                "ssl": True,
                "username": "imap-user",
                "password": "imap-secret",
            },
        },
        "error": None,
    }


class StrictUidValidityTests(unittest.TestCase):
    def test_canonical_ascii_decimal_is_preserved_exactly(self):
        for value in ("1", "77", "99999999999999999999"):
            with self.subTest(value=value):
                self.assertEqual(parse_uid_validity(value), value)
                self.assertEqual(parse_uid_validity(value.encode("ascii")), value)
                self.assertTrue(is_canonical_uid_validity(value))

    def test_malformed_identifiers_fail_closed(self):
        invalid_values = (
            b"\xff77",
            b"77\xff",
            "\ufffd77",
            "\uff11\uff12",
            b"77\x00",
            b"7\n7",
            b" 77",
            b"77 ",
            b"\t77",
            b"0",
            b"01",
            b"+1",
            b"2.0",
            b"2e0",
            b"-1",
            b"",
            b"100000000000000000000",
            77,
            bytearray(b"77"),
            memoryview(b"77"),
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.assertIsNone(parse_uid_validity(value))
                self.assertFalse(is_canonical_uid_validity(value))
                self.assertEqual(
                    imap_connect_preview.resolve_custom_imap_thread_ids(
                        [
                            {
                                "message_id": None,
                                "in_reply_to": None,
                                "references": [],
                                "imap_uid": "42",
                                "message_id_ambiguous": False,
                            }
                        ],
                        mailbox_key="mailbox-1",
                        folder="INBOX",
                        uid_validity=value,
                    ),
                    [None],
                )

    def test_response_shape_and_exact_tag_are_required(self):
        self.assertEqual(
            parse_uid_validity_response("UIDVALIDITY", [b"77"]),
            "77",
        )
        for tag, values in (
            ("OK", [b"77"]),
            ("uidvalidity", [b"77"]),
            (b"UIDVALIDITY", [b"77"]),
            ("UIDVALIDITY ", [b"77"]),
            ("UIDVALIDITY", []),
            ("UIDVALIDITY", [b"77", b"78"]),
            ("UIDVALIDITY", b"77"),
            ("UIDVALIDITY", {"0": b"77"}),
            ("UIDVALIDITY", [b"77 trailing"]),
        ):
            with self.subTest(tag=tag, values=values):
                self.assertIsNone(parse_uid_validity_response(tag, values))

    def test_selected_mailbox_reader_requests_only_exact_response_code(self):
        class Mailbox:
            def __init__(self, result):
                self.result = result
                self.names = []

            def response(self, name):
                self.names.append(name)
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        valid = Mailbox(("UIDVALIDITY", [b"77"]))
        self.assertEqual(read_selected_mailbox_uid_validity(valid), "77")
        self.assertEqual(valid.names, ["UIDVALIDITY"])

        failed = Mailbox(RuntimeError("provider failure"))
        self.assertIsNone(read_selected_mailbox_uid_validity(failed))
        self.assertEqual(failed.names, ["UIDVALIDITY"])


class ActiveUidValidityPathTests(unittest.TestCase):
    ROUTE_PAYLOADS = (
        (
            ACTION_ROUTE,
            {
                "mailboxId": "demo",
                "folder": "INBOX",
                "uid": "123",
                "action": "mark_read",
            },
        ),
        (
            ATTACHMENT_ROUTE,
            {
                "mailboxId": "demo",
                "folder": "INBOX",
                "uid": "123",
                "attachmentId": "part-2",
            },
        ),
    )

    def test_active_handlers_reject_noncanonical_request_identifiers_before_imap_resolution(self):
        for route, base_payload in self.ROUTE_PAYLOADS:
            for invalid in (
                "0",
                "01",
                "+1",
                " 1",
                "1 ",
                "\uff11\uff12",
                "1\n",
                "100000000000000000000",
            ):
                with self.subTest(route=route.__name__, uid_validity=invalid):
                    handler = _FakeHandler({**base_payload, "uidValidity": invalid})
                    with patch.object(
                        route,
                        "resolve_owned_mailbox",
                        return_value={
                            "status": "ok",
                            "inbox": {"provider": "custom_imap"},
                        },
                    ), patch.object(
                        route, "resolve_authenticated_imap_mailbox"
                    ) as mailbox_resolver:
                        route.handler.do_POST(handler)
                    self.assertEqual(handler.status, 400)
                    self.assertEqual(handler.response()["error"]["code"], "invalid_request")
                    mailbox_resolver.assert_not_called()

    def test_active_handlers_fail_closed_on_malformed_provider_identifiers(self):
        malformed_responses = (
            ("OK", [b"456"]),
            ("uidvalidity", [b"456"]),
            ("UIDVALIDITY", [b"\xff456"]),
            ("UIDVALIDITY", [b"0456"]),
            ("UIDVALIDITY", [b"456 "]),
            ("UIDVALIDITY", [b"456", b"457"]),
        )
        for route, base_payload in self.ROUTE_PAYLOADS:
            payload = {**base_payload, "uidValidity": "456"}
            for response in malformed_responses:
                with self.subTest(route=route.__name__, response=response):
                    mailbox = Mock()
                    mailbox.select.return_value = ("OK", [])
                    mailbox.response.return_value = response
                    handler = _FakeHandler(payload)
                    with patch.object(
                        route,
                        "resolve_owned_mailbox",
                        return_value={
                            "status": "ok",
                            "inbox": {"provider": "custom_imap"},
                        },
                    ), patch.object(
                        route,
                        "resolve_authenticated_imap_mailbox",
                        return_value=_resolved_mailbox(),
                    ), patch.object(
                        route,
                        "connect_mailbox_with_settings",
                        return_value=mailbox,
                    ):
                        route.handler.do_POST(handler)
                    self.assertEqual(handler.status, 409)
                    self.assertEqual(
                        handler.response()["error"]["code"],
                        "uid_validity_changed",
                    )
                    mailbox.response.assert_called_once_with("UIDVALIDITY")
                    mailbox.uid.assert_not_called()

    def test_active_preview_never_builds_uid_identity_from_malformed_provider_value(self):
        message = message_from_string("Subject: Headerless\n\nBody")
        malformed_responses = (
            ("OK", [b"77"]),
            ("uidvalidity", [b"77"]),
            ("UIDVALIDITY", [b"\xff77"]),
            ("UIDVALIDITY", ["\ufffd77"]),
            ("UIDVALIDITY", [b"77\x00"]),
            ("UIDVALIDITY", [b" 77"]),
            ("UIDVALIDITY", [b"77 "]),
            ("UIDVALIDITY", [b"0"]),
            ("UIDVALIDITY", [b"01"]),
            ("UIDVALIDITY", [b"+77"]),
            ("UIDVALIDITY", [b"100000000000000000000"]),
            ("UIDVALIDITY", [b"77", b"78"]),
        )
        for response in malformed_responses:
            mailbox = Mock()
            mailbox.uid.return_value = ("OK", [b"42"])
            mailbox.response.return_value = response
            with self.subTest(response=response), patch.object(
                imap_connect_preview,
                "open_mailbox_connection",
                return_value=mailbox,
            ), patch.object(
                imap_connect_preview,
                "fetch_recent_messages",
                return_value={
                    "messages": [(message, True, "42", False)],
                    "warnings": [],
                    "error": None,
                },
            ), patch.object(
                imap_connect_preview,
                "resolve_preview_routing",
                return_value={
                    "ui_signal": "NEW",
                    "internalClassification": "unknown",
                },
            ), self.assertLogs(imap_connect_preview.logger.name, level="WARNING"):
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
            self.assertEqual(payload["messages"], [])
            self.assertNotIn("uidValidity", payload)
            mailbox.response.assert_called_once_with("UIDVALIDITY")


if __name__ == "__main__":
    unittest.main()
