from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path

from api.out_of_office_store import OutOfOfficeStoreUnavailable


def _load_route():
    route_path = Path(__file__).with_name("out-of-office.py")
    spec = importlib.util.spec_from_file_location("api_out_of_office_route", route_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("route could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = _load_route()


class FakeHandler:
    def __init__(self, *, path="/api/out-of-office", body=None):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        self.path = path
        self.headers = {"content-length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status_code = None
        self.response_headers = {}

    def send_response(self, status_code):
        self.status_code = status_code

    def send_header(self, name, value):
        self.response_headers[name.lower()] = value

    def end_headers(self):
        return None

    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class FakeStore:
    def __init__(self, stored=None, cursor=None):
        self.stored = stored
        self.cursor = cursor
        self.put_calls = []
        self.put_with_cursor_calls = []

    def get(self, owner_email, mailbox_id):
        return self.stored

    def put(self, owner_email, mailbox_id, settings):
        self.put_calls.append((owner_email, mailbox_id, settings))
        self.stored = settings

    def get_cursor(self, owner_email, mailbox_id):
        return self.cursor

    def put_with_cursor(self, owner_email, mailbox_id, settings, cursor):
        self.put_with_cursor_calls.append((owner_email, mailbox_id, settings, cursor))
        self.stored = settings
        self.cursor = cursor


def owned(mailbox_id="mailbox-1"):
    return {
        "status": "ok",
        "user": {"email": "owner@example.com", "name": "Owner", "userType": "member"},
        "inbox": {"id": mailbox_id, "provider": "google", "email": "label@example.com"},
        "config": {},
        "error": None,
    }


class OutOfOfficeHttpTests(unittest.TestCase):
    def setUp(self):
        self.original_resolver = route.resolve_owned_managed_inbox_record
        self.original_store_builder = route.build_out_of_office_store
        self.original_adapter_resolver = route.resolve_out_of_office_adapter

    def tearDown(self):
        route.resolve_owned_managed_inbox_record = self.original_resolver
        route.build_out_of_office_store = self.original_store_builder
        route.resolve_out_of_office_adapter = self.original_adapter_resolver

    def test_get_requires_mailbox_id(self):
        handler = FakeHandler(path="/api/out-of-office")
        route.handler.do_GET(handler)
        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.payload()["error"]["code"], "invalid_mailbox_id")

    def test_get_maps_unauthorized_before_store_access(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: {
            "status": "unauthorized",
            "user": None,
            "inbox": None,
            "config": None,
            "error": None,
        }
        route.build_out_of_office_store = lambda: self.fail("store must not be opened")
        handler = FakeHandler(path="/api/out-of-office?mailboxId=mailbox-1")
        route.handler.do_GET(handler)
        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.payload()["error"]["code"], "unauthorized")

    def test_get_returns_disabled_default_when_no_record_exists(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: owned()
        store = FakeStore()
        route.build_out_of_office_store = lambda: store
        handler = FakeHandler(path="/api/out-of-office?mailboxId=mailbox-1")
        route.handler.do_GET(handler)
        payload = handler.payload()
        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mailboxId"], "mailbox-1")
        self.assertFalse(payload["settings"]["enabled"])
        self.assertEqual(payload["settings"]["subject"], "")
        self.assertEqual(payload["settings"]["message"], "")

    def test_post_saves_only_after_mailbox_ownership_is_verified(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: owned()
        store = FakeStore()
        route.build_out_of_office_store = lambda: store

        class Adapter:
            def baseline(self, _inbox, *, owner_email, now):
                from api.out_of_office_store import build_gmail_out_of_office_cursor
                return build_gmail_out_of_office_cursor("100", now=now)

        route.resolve_out_of_office_adapter = lambda _inbox: Adapter()
        handler = FakeHandler(
            body={
                "mailboxId": "mailbox-1",
                "settings": {
                    "enabled": True,
                    "startsAt": "2026-10-10T08:00:00+02:00",
                    "endsAt": "2026-10-20T18:00:00+02:00",
                    "subject": " Out of office ",
                    "message": " Back soon. ",
                },
            }
        )
        route.handler.do_POST(handler)
        payload = handler.payload()
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(len(store.put_with_cursor_calls), 1)
        owner_email, mailbox_id, settings, cursor = store.put_with_cursor_calls[0]
        self.assertEqual(owner_email, "owner@example.com")
        self.assertEqual(mailbox_id, "mailbox-1")
        self.assertEqual(settings["startsAt"], "2026-10-10T06:00:00Z")
        self.assertEqual(settings["endsAt"], "2026-10-20T16:00:00Z")
        self.assertEqual(settings["activatedAt"], settings["updatedAt"])
        self.assertEqual(cursor["historyId"], "100")
        self.assertEqual(settings["subject"], "Out of office")
        self.assertEqual(settings["message"], "Back soon.")
        self.assertEqual(payload["settings"], settings)

    def test_changing_end_time_rebaselines_before_saving(self):
        from datetime import datetime, timezone
        from api.out_of_office_store import (
            build_gmail_out_of_office_cursor,
            normalize_out_of_office_settings,
        )

        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: owned()
        existing = normalize_out_of_office_settings(
            {
                "enabled": True,
                "startsAt": None,
                "endsAt": "2026-10-20T16:00:00Z",
                "activatedAt": "2026-10-08T10:00:00Z",
                "subject": "Out of office",
                "message": "Back soon.",
            },
            now=datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc),
        )
        store = FakeStore(
            stored=existing,
            cursor=build_gmail_out_of_office_cursor(
                "100",
                now=datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc),
            ),
        )
        route.build_out_of_office_store = lambda: store
        baseline_calls = []

        class Adapter:
            def baseline(self, _inbox, *, owner_email, now):
                baseline_calls.append((owner_email, now))
                return build_gmail_out_of_office_cursor("200", now=now)

        route.resolve_out_of_office_adapter = lambda _inbox: Adapter()
        handler = FakeHandler(
            body={
                "mailboxId": "mailbox-1",
                "settings": {
                    "enabled": True,
                    "startsAt": None,
                    "endsAt": "2026-10-25T16:00:00Z",
                    "subject": "Out of office",
                    "message": "Back soon.",
                },
            }
        )

        route.handler.do_POST(handler)

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(len(baseline_calls), 1)
        self.assertEqual(len(store.put_with_cursor_calls), 1)
        self.assertEqual(store.put_with_cursor_calls[0][3]["historyId"], "200")
        self.assertNotEqual(
            store.put_with_cursor_calls[0][2]["activatedAt"],
            existing["activatedAt"],
        )

    def test_post_rejects_unknown_settings_fields(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: owned()
        route.build_out_of_office_store = lambda: self.fail("store must not be opened")
        handler = FakeHandler(
            body={
                "mailboxId": "mailbox-1",
                "settings": {
                    "enabled": False,
                    "startsAt": None,
                    "endsAt": None,
                    "subject": "",
                    "message": "",
                    "secret": "nope",
                },
            }
        )
        route.handler.do_POST(handler)
        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.payload()["error"]["code"], "invalid_settings")

    def test_post_maps_missing_mailbox_to_404(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: {
            "status": "not_found",
            "user": {"email": "owner@example.com"},
            "inbox": None,
            "config": {},
            "error": None,
        }
        route.build_out_of_office_store = lambda: self.fail("store must not be opened")
        handler = FakeHandler(
            body={
                "mailboxId": "missing",
                "settings": {
                    "enabled": False,
                    "startsAt": None,
                    "endsAt": None,
                    "subject": "",
                    "message": "",
                },
            }
        )
        route.handler.do_POST(handler)
        self.assertEqual(handler.status_code, 404)
        self.assertEqual(handler.payload()["error"]["code"], "mailbox_not_found")

    def test_store_failure_is_503(self):
        route.resolve_owned_managed_inbox_record = lambda _headers, _mailbox_id: owned()

        def unavailable():
            raise OutOfOfficeStoreUnavailable()

        route.build_out_of_office_store = unavailable
        handler = FakeHandler(path="/api/out-of-office?mailboxId=mailbox-1")
        route.handler.do_GET(handler)
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(handler.payload()["error"]["code"], "out_of_office_unavailable")


if __name__ == "__main__":
    unittest.main()
