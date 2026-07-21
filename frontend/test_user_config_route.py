import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

FRONTEND_DIR = Path(__file__).resolve().parent
API_DIR = FRONTEND_DIR / "api"
CONFIG_PATH = API_DIR / "user" / "config.py"
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import user_config_store


def load_config_route(module_name="user_config_route_under_test"):
    spec = importlib.util.spec_from_file_location(module_name, CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


config_route = load_config_route()


class FakeHandler:
    def __init__(self, body=b"", headers=None):
        self.headers = {"content-length": str(len(body)), **(headers or {})}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code = None
        self.response_headers = []
        self.end_headers_count = 0

    def send_response(self, status_code):
        self.status_code = status_code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.end_headers_count += 1

    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


SESSION_USER = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
STORE = {"rest_url": "https://kv.example", "rest_token": "token"}


class GetRouteTests(unittest.TestCase):
    def invoke(self, auth=(SESSION_USER, None), store=(STORE, None), read_result=None):
        read_result = read_result or {
            "status": "ok",
            "config": {"v": 1, "email": "owner@example.com"},
            "error": None,
        }
        handler = FakeHandler()
        with patch.object(config_route, "resolve_authenticated_user", return_value=auth), patch.object(
            config_route,
            "resolve_user_config_store",
            return_value=store,
        ), patch.object(
            config_route,
            "read_user_config_record",
            return_value=read_result,
        ) as read_mock, patch.object(
            config_route,
            "write_user_config_record",
            return_value={"status": "ok", "record": {"result": "OK"}, "error": None},
        ) as write_mock:
            config_route.handler.do_GET(handler)
        return handler, read_mock, write_mock

    def test_found_record_and_headers_are_unchanged(self):
        handler, read_mock, write_mock = self.invoke()
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.payload(),
            {"ok": True, "config": {"v": 1, "email": "owner@example.com"}},
        )
        self.assertIn(("Content-Type", "application/json"), handler.response_headers)
        self.assertIn(("Cache-Control", "no-store"), handler.response_headers)
        self.assertIn(
            ("Content-Length", str(len(handler.wfile.getvalue()))),
            handler.response_headers,
        )
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()

    def test_missing_unconfigured_and_read_failures_still_return_null(self):
        results = [
            (
                (STORE, None),
                {
                    "status": "missing",
                    "config": None,
                    "error": {"code": "user_config_not_found", "message": "missing"},
                },
            ),
            (
                (STORE, None),
                {
                    "status": "unavailable",
                    "config": None,
                    "error": {
                        "code": "user_config_store_unavailable",
                        "message": "unavailable",
                    },
                },
            ),
            (
                (STORE, None),
                {
                    "status": "malformed",
                    "config": None,
                    "error": {"code": "user_config_malformed", "message": "malformed"},
                },
            ),
            (
                (
                    None,
                    {
                        "code": "user_config_store_unavailable",
                        "message": "not configured",
                    },
                ),
                None,
            ),
        ]
        for store, read_result in results:
            with self.subTest(store=store, read_result=read_result):
                handler, _, write_mock = self.invoke(store=store, read_result=read_result)
                self.assertEqual(handler.status_code, 200)
                self.assertEqual(handler.payload(), {"ok": True, "config": None})
                write_mock.assert_not_called()

    def test_unauthorized_shape_is_exact(self):
        auth_error = {"code": "missing_session", "message": "missing"}
        handler, _, write_mock = self.invoke(auth=(None, auth_error))
        self.assertEqual(handler.status_code, 401)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "unauthorized",
                    "message": "A valid member session is required.",
                },
            },
        )
        write_mock.assert_not_called()

    def test_authentication_unavailable_is_503_before_storage_io(self):
        auth_error = {
            "code": "session_auth_unavailable",
            "message": "internal detail must not escape",
        }
        handler, read_mock, write_mock = self.invoke(auth=(None, auth_error))
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "authentication_unavailable",
                    "message": "Authentication is temporarily unavailable.",
                },
            },
        )
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_legacy_passwords_are_stripped_rewritten_and_never_returned(self):
        legacy = {
            "v": 1,
            "email": "owner@example.com",
            "managedInboxes": [
                {
                    "id": "demo",
                    "customImap": {"host": "imap.example.com", "password": "imap-secret"},
                    "customSmtp": {"host": "smtp.example.com", "password": "smtp-secret"},
                }
            ],
        }
        handler, _, write_mock = self.invoke(
            read_result={"status": "ok", "config": legacy, "error": None},
        )
        self.assertEqual(handler.status_code, 200)
        returned = handler.payload()["config"]
        self.assertNotIn("imap-secret", json.dumps(returned))
        self.assertNotIn("smtp-secret", json.dumps(returned))
        write_mock.assert_called_once()
        self.assertEqual(write_mock.call_args.args[2], returned)


class PostRouteTests(unittest.TestCase):
    def invoke(
        self,
        payload,
        *,
        raw_body=None,
        auth=(SESSION_USER, None),
        store=(STORE, None),
        read_result=None,
        write_result=None,
    ):
        body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
        handler = FakeHandler(body)
        read_result = read_result or {"status": "missing", "config": None, "error": None}
        write_result = write_result or {
            "status": "ok",
            "record": {"result": "OK"},
            "error": None,
        }
        with patch.object(config_route, "resolve_authenticated_user", return_value=auth), patch.object(
            config_route,
            "resolve_user_config_store",
            return_value=store,
        ), patch.object(
            config_route,
            "read_user_config_record",
            return_value=read_result,
        ) as read_mock, patch.object(
            config_route,
            "write_user_config_record",
            return_value=write_result,
        ) as write_mock:
            config_route.handler.do_POST(handler)
        return handler, read_mock, write_mock

    def test_invalid_json_and_non_object_json_are_unchanged(self):
        for raw_body in (b"{", b"[]"):
            with self.subTest(raw_body=raw_body):
                handler, _, write_mock = self.invoke({}, raw_body=raw_body)
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(handler.payload()["error"]["code"], "invalid_request")
                write_mock.assert_not_called()

    def test_authentication_failure_precedes_body_and_storage_io(self):
        for auth_error, expected_status in (
            ({"code": "missing_session", "message": "missing"}, 401),
            (
                {
                    "code": "session_auth_unavailable",
                    "message": "private outage detail",
                },
                503,
            ),
        ):
            with self.subTest(auth_error=auth_error):
                handler, read_mock, write_mock = self.invoke(
                    {"config": {"uiPreferences": {"themeMode": "Dark"}}},
                    auth=(None, auth_error),
                )
                self.assertEqual(handler.status_code, expected_status)
                self.assertEqual(handler.rfile.tell(), 0)
                read_mock.assert_not_called()
                write_mock.assert_not_called()
                self.assertNotIn("private outage detail", json.dumps(handler.payload()))

    def test_store_unavailable_is_existing_503(self):
        error = {"code": "user_config_store_unavailable", "message": "not configured"}
        handler, _, write_mock = self.invoke({}, store=(None, error))
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "user_config_store_unavailable",
                    "message": "User config storage is not configured.",
                },
            },
        )
        write_mock.assert_not_called()

    def test_read_unavailable_fails_closed_without_writing(self):
        read_error = {
            "status": "unavailable",
            "config": None,
            "error": {"code": "user_config_store_unavailable", "message": "read failed"},
        }
        handler, read_mock, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Dark"}}},
            read_result=read_error,
        )
        self.assertEqual(handler.status_code, 503)
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "user_config_store_unavailable",
                    "message": "User config storage is temporarily unavailable.",
                },
            },
        )

    def test_merge_sanitization_owner_and_timestamp_behavior_are_unchanged(self):
        existing = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "old",
            "managedInboxes": [{
                "id": "mailbox-a",
                "email": "verified@gmail.com",
                "provider": "google",
                "connectionMethod": "oauth",
                "connectionType": "oauth",
                "connected": True,
                "connectionStatus": "connected",
                "oauthOwnerEmail": "owner@example.com",
                "title": "Old title",
            }],
            "smartFolders": [{"id": "keep"}],
        }
        read_result = {"status": "ok", "config": existing, "error": None}
        payload = {
            "config": {
                "email": "attacker@example.com",
                "access_token": "secret",
                "managedInboxes": [
                    {
                        "id": "mailbox-a",
                        "email": "artist@example.com",
                        "provider": "google",
                        "connected": True,
                        "connectionStatus": "connected",
                        "title": "New title",
                        "oauthAuthorizationUrl": "https://secret.example",
                        "customImap": {"host": "imap.example", "password": "secret"},
                        "customSmtp": {"host": "smtp.example", "password": "secret"},
                    }
                ],
            }
        }
        handler, _, write_mock = self.invoke(payload, read_result=read_result)
        written = write_mock.call_args.args[2]

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(written["v"], 1)
        self.assertEqual(written["email"], "owner@example.com")
        self.assertNotEqual(written["updatedAt"], "old")
        self.assertTrue(written["updatedAt"].endswith("Z"))
        self.assertNotIn("access_token", written)
        self.assertEqual(written["smartFolders"], [{"id": "keep"}])
        inbox = written["managedInboxes"][0]
        self.assertEqual(inbox["email"], "verified@gmail.com")
        self.assertEqual(inbox["provider"], "google")
        self.assertEqual(inbox["oauthOwnerEmail"], "owner@example.com")
        self.assertEqual(inbox["title"], "New title")
        self.assertNotIn("oauthAuthorizationUrl", inbox)
        self.assertEqual(handler.payload(), {"ok": True, "config": written})

    def test_protected_google_field_attacks_preserve_server_values(self):
        existing_inbox = {
            "id": "mailbox-a",
            "email": "verified@gmail.com",
            "provider": "google",
            "connectionMethod": "oauth",
            "connectionType": "oauth",
            "connected": True,
            "connectionStatus": "connected",
            "oauthOwnerEmail": "owner@example.com",
            "title": "Server title",
            "internalRole": "management",
            "focusPreferences": {"promo": "medium"},
        }
        read_result = {
            "status": "ok",
            "config": {
                "v": 1,
                "email": "owner@example.com",
                "managedInboxes": [existing_inbox],
            },
            "error": None,
        }
        payload = {
            "config": {
                "managedInboxes": [
                    {
                        "id": " MAILBOX-A ",
                        "title": {"nested": "attack"},
                        "internalRole": ["producer"],
                        "focusPreferences": {"promo": "low", "unknown": "high"},
                        "email": "attacker@gmail.com",
                        "provider": "custom_imap",
                        "connected": False,
                        "connectionStatus": "connection_failed",
                        "oauthOwnerEmail": "attacker@example.com",
                    },
                    {"id": "mailbox-a", "title": "duplicate"},
                ]
            }
        }
        handler, _, write_mock = self.invoke(payload, read_result=read_result)
        written = write_mock.call_args.args[2]

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(written["managedInboxes"], [existing_inbox])

    def test_write_failure_shape_and_success_headers_are_unchanged(self):
        error = {"code": "user_config_store_unavailable", "message": "write failed"}
        handler, _, write_mock = self.invoke(
            {},
            write_result={"status": "unavailable", "record": None, "error": error},
        )
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(handler.payload(), {"ok": False, "error": error})
        write_mock.assert_called_once()

        success_handler, _, success_write = self.invoke({})
        self.assertEqual(success_handler.status_code, 200)
        self.assertIn(("Cache-Control", "no-store"), success_handler.response_headers)
        success_write.assert_called_once()


class PostReadStatusHandlerTests(unittest.TestCase):
    def invoke(self, payload, read_result):
        body = json.dumps(payload).encode("utf-8")
        request = FakeHandler(
            body,
            headers={"cookie": "__Host-cuevion_session=opaque"},
        )
        with patch.object(
            config_route,
            "resolve_authenticated_user",
            return_value=(SESSION_USER, None),
        ), patch.object(
            config_route,
            "resolve_user_config_store",
            return_value=(STORE, None),
        ), patch.object(
            config_route,
            "read_user_config_record",
            return_value=read_result,
        ) as read_mock, patch.object(
            config_route,
            "write_user_config_record",
            return_value={"status": "ok", "record": {"result": "OK"}, "error": None},
        ) as write_mock:
            config_route.handler.do_POST(request)
        return request, read_mock, write_mock

    def assert_no_store_detail(self, request, *details):
        response_text = json.dumps(request.payload())
        self.assertEqual(request.status_code, 503)
        self.assertEqual(
            request.payload()["error"]["code"],
            "user_config_store_unavailable",
        )
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        for detail in details:
            self.assertNotIn(detail, response_text)

    def test_missing_initializes_and_writes_first_time_config(self):
        request, read_mock, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Dark"}}},
            {
                "status": "missing",
                "config": None,
                "error": {"code": "user_config_not_found", "message": "not found"},
            },
        )
        self.assertEqual(request.status_code, 200)
        read_mock.assert_called_once_with(
            STORE,
            "owner@example.com",
        )
        write_mock.assert_called_once()
        written = write_mock.call_args.args[2]
        self.assertEqual(written["email"], "owner@example.com")
        self.assertEqual(written["managedInboxes"], [])
        self.assertEqual(written["uiPreferences"], {"themeMode": "Dark"})
        self.assertEqual(request.payload(), {"ok": True, "config": written})
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)

    def test_unavailable_cannot_erase_protected_google_config(self):
        protected = {
            "id": "gmail-1",
            "email": "verified@gmail.com",
            "provider": "google",
            "connected": True,
            "connectionStatus": "connected",
            "oauthOwnerEmail": "owner@example.com",
        }
        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": []}},
            {
                "status": "unavailable",
                "config": {"managedInboxes": [protected]},
                "error": {
                    "code": "user_config_store_unavailable",
                    "message": "raw read failure https://storage.invalid",
                },
            },
        )
        self.assert_no_store_detail(request, "raw read failure", "storage.invalid")
        write_mock.assert_not_called()

    def test_malformed_fails_closed_without_exposing_or_writing(self):
        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": []}},
            {
                "status": "malformed",
                "config": {"raw": "stored-secret"},
                "error": {
                    "code": "user_config_malformed",
                    "message": "JSON parse failed at https://storage.invalid",
                },
            },
        )
        self.assert_no_store_detail(
            request,
            "stored-secret",
            "JSON parse failed",
            "storage.invalid",
        )
        write_mock.assert_not_called()

    def test_unexpected_typed_read_status_fails_closed_without_writing(self):
        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": []}},
            {
                "status": "future_status",
                "config": {"raw": "unexpected-record"},
                "error": {"code": "future_error", "message": "raw future detail"},
            },
        )
        self.assert_no_store_detail(request, "unexpected-record", "raw future detail")
        write_mock.assert_not_called()

    def test_invalid_ok_payload_fails_closed_without_writing(self):
        for invalid_payload, raw_marker in ((None, "null"), ([], "[]")):
            with self.subTest(invalid_payload=invalid_payload):
                request, _, write_mock = self.invoke(
                    {"config": {"managedInboxes": []}},
                    {"status": "ok", "config": invalid_payload},
                )
                self.assert_no_store_detail(
                    request,
                    raw_marker,
                    "storage.invalid",
                    "parser",
                    "exception",
                    "traceback",
                )
                self.assertNotIn("config", request.payload())
                write_mock.assert_not_called()

    def test_ok_preserves_google_identity_and_accepts_safe_presentation_update(self):
        protected = {
            "id": "gmail-1",
            "email": "verified@gmail.com",
            "provider": "google",
            "connectionMethod": "oauth",
            "connectionType": "oauth",
            "connected": True,
            "connectionStatus": "connected",
            "oauthOwnerEmail": "owner@example.com",
            "title": "Old title",
        }
        request, _, write_mock = self.invoke(
            {
                "config": {
                    "managedInboxes": [
                        {
                            "id": "gmail-1",
                            "email": "attacker@gmail.com",
                            "provider": "custom_imap",
                            "connected": False,
                            "title": "New title",
                        }
                    ]
                }
            },
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": [protected],
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        saved = write_mock.call_args.args[2]["managedInboxes"][0]
        self.assertEqual(saved["email"], "verified@gmail.com")
        self.assertEqual(saved["provider"], "google")
        self.assertTrue(saved["connected"])
        self.assertEqual(saved["oauthOwnerEmail"], "owner@example.com")
        self.assertEqual(saved["title"], "New title")

    def test_unchanged_existing_custom_imap_snapshot_remains_compatible(self):
        custom_imap = {
            "id": "imap-1",
            "email": "artist@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionStatus": "connected",
            "customImap": {"host": "imap.example.com", "username": "artist"},
            "customSmtp": {"host": "smtp.example.com", "username": "artist"},
        }
        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": [custom_imap]}},
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": [custom_imap],
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        saved = write_mock.call_args.args[2]["managedInboxes"][0]
        self.assertEqual(saved["id"], custom_imap["id"])
        self.assertEqual(saved["email"], custom_imap["email"])
        self.assertEqual(saved["provider"], "custom_imap")
        self.assertEqual(saved["customImap"]["host"], "imap.example.com")
        self.assertEqual(saved["customImap"]["username"], "artist")
        self.assertEqual(saved["customImap"]["password"], "")
        self.assertEqual(saved["customSmtp"]["host"], "smtp.example.com")
        self.assertEqual(saved["customSmtp"]["username"], "artist")
        self.assertEqual(saved["customSmtp"]["password"], "")

    def test_new_connected_mailboxes_are_ignored_and_existing_records_cannot_be_omitted(self):
        existing = {
            "id": "imap-existing",
            "title": "Existing mailbox",
            "email": "verified@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
        }
        requested_new = [
            {
                "id": "imap-new",
                "title": "Claimed IMAP mailbox",
                "email": "claimed@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionMethod": "imap",
                "connectionStatus": "connected",
            },
            {
                "id": "gmail-new",
                "title": "Claimed Gmail mailbox",
                "email": "claimed@gmail.com",
                "provider": "google",
                "connected": True,
                "connectionMethod": "oauth",
                "connectionStatus": "connected",
            },
        ]

        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": requested_new}},
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": [existing],
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        self.assertEqual(write_mock.call_args.args[2]["managedInboxes"], [existing])

        missing_request, _, missing_write = self.invoke(
            {"config": {"managedInboxes": requested_new}},
            {
                "status": "missing",
                "config": None,
                "error": {"code": "user_config_not_found", "message": "not found"},
            },
        )
        self.assertEqual(missing_request.status_code, 200)
        self.assertEqual(missing_write.call_args.args[2]["managedInboxes"], [])

    def test_existing_custom_imap_allows_only_validated_presentation_updates(self):
        existing = {
            "id": "imap-1",
            "title": "Old title",
            "email": "verified@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "connectionMessage": None,
            "customImap": {
                "host": "imap.verified.example",
                "port": "993",
                "ssl": True,
                "username": "verified-user",
                "password": "",
            },
            "customSmtp": {
                "host": "smtp.verified.example",
                "port": "587",
                "security": "starttls",
                "username": "verified-user",
                "useSameCredentials": True,
                "password": "",
            },
            "internalRole": "management",
            "focusPreferences": {"promo": "medium"},
        }
        request, _, write_mock = self.invoke(
            {
                "config": {
                    "managedInboxes": [
                        {
                            "id": "IMAP-1",
                            "title": "  New title  ",
                            "email": "attacker@example.com",
                            "provider": "google",
                            "connected": False,
                            "connectionMethod": "oauth",
                            "connectionStatus": "connection_failed",
                            "connectionMessage": "attacker-controlled",
                            "customImap": {
                                "host": "imap.attacker.example",
                                "port": "143",
                                "ssl": False,
                                "username": "attacker",
                            },
                            "customSmtp": {
                                "host": "smtp.attacker.example",
                                "port": "25",
                                "security": "ssl",
                                "username": "attacker",
                                "useSameCredentials": False,
                            },
                            "internalRole": "producer",
                            "focusPreferences": {"promo": "low"},
                        }
                    ]
                }
            },
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": [existing],
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        saved = write_mock.call_args.args[2]["managedInboxes"][0]
        for authoritative_field in (
            "id",
            "email",
            "provider",
            "connected",
            "connectionMethod",
            "connectionStatus",
            "connectionMessage",
            "customImap",
            "customSmtp",
        ):
            self.assertEqual(saved[authoritative_field], existing[authoritative_field])
        self.assertEqual(saved["title"], "New title")
        self.assertEqual(saved["internalRole"], "producer")
        self.assertEqual(saved["focusPreferences"], {"promo": "low"})

    def test_identity_only_attack_preserves_the_complete_existing_record(self):
        existing = {
            "id": "imap-1",
            "title": "Server title",
            "email": "verified@example.com",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "connectionMessage": None,
            "customImap": {
                "host": "imap.verified.example",
                "port": "993",
                "ssl": True,
                "username": "verified-user",
                "password": "",
            },
            "customSmtp": {
                "host": "smtp.verified.example",
                "port": "587",
                "security": "starttls",
                "username": "verified-user",
                "useSameCredentials": True,
                "password": "",
            },
            "internalRole": "management",
            "focusPreferences": {"promo": "medium"},
        }
        request, _, write_mock = self.invoke(
            {
                "config": {
                    "managedInboxes": [
                        {
                            "id": "imap-1",
                            "email": "attacker@example.com",
                            "provider": "google",
                            "connected": False,
                            "connectionMethod": "oauth",
                            "connectionStatus": "connection_failed",
                            "connectionMessage": "attacker-controlled",
                            "customImap": {
                                "host": "imap.attacker.example",
                                "port": "143",
                                "ssl": False,
                                "username": "attacker",
                            },
                            "customSmtp": {
                                "host": "smtp.attacker.example",
                                "port": "25",
                                "security": "ssl",
                                "username": "attacker",
                                "useSameCredentials": False,
                            },
                        }
                    ]
                }
            },
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": [existing],
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        write_mock.assert_called_once()
        self.assertEqual(write_mock.call_args.args[2]["managedInboxes"], [existing])

    def test_duplicate_requested_or_existing_ids_fail_closed_without_reordering(self):
        existing = [
            {
                "id": "imap-1",
                "title": "First",
                "email": "first@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionStatus": "connected",
            },
            {
                "id": " IMAP-1 ",
                "title": "Second",
                "email": "second@example.com",
                "provider": "custom_imap",
                "connected": False,
                "connectionStatus": "connection_failed",
            },
        ]
        requested = [
            {"id": "imap-1", "title": "Attacker one"},
            {"id": "IMAP-1", "title": "Attacker two"},
        ]
        request, _, write_mock = self.invoke(
            {"config": {"managedInboxes": requested}},
            {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "managedInboxes": existing,
                },
                "error": None,
            },
        )
        self.assertEqual(request.status_code, 200)
        self.assertEqual(write_mock.call_args.args[2]["managedInboxes"], existing)


class ModuleCompatibilityTests(unittest.TestCase):
    def test_options_and_unsupported_method_behavior_are_unchanged(self):
        handler = FakeHandler()
        config_route.handler.do_OPTIONS(handler)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.payload(), {"ok": True})
        self.assertNotIn("do_PUT", config_route.handler.__dict__)

    def test_import_has_no_store_or_network_activity(self):
        with patch.object(user_config_store, "resolve_authenticated_user") as auth_mock, patch.object(
            user_config_store,
            "resolve_user_config_store",
        ) as store_mock, patch.object(
            user_config_store,
            "read_user_config_record",
        ) as read_mock, patch.object(
            user_config_store,
            "write_user_config_record",
        ) as write_mock:
            load_config_route("user_config_route_import_activity_test")
        auth_mock.assert_not_called()
        store_mock.assert_not_called()
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_config_route_uses_shared_primitives_without_duplicate_store_code(self):
        source = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertIn("from user_config_store import", source)
        for removed_definition in (
            "def _resolve_durable_store_config",
            "def _perform_rest_request",
            "def _read_durable_record",
            "def _write_durable_record",
            "def _get_authenticated_user",
            "def _build_user_config_key",
        ):
            self.assertNotIn(removed_definition, source)

        production_imports = []
        for path in API_DIR.rglob("*.py"):
            if path.name.startswith("test_"):
                continue
            if path in {
                API_DIR / "user_config_store.py",
                API_DIR / "inboxes" / "oauth_google.py",
            }:
                continue
            if "user_config_store" in path.read_text(encoding="utf-8"):
                production_imports.append(path.relative_to(FRONTEND_DIR).as_posix())
        self.assertEqual(
            sorted(production_imports),
            [
                "api/inboxes/authenticated_gmail.py",
                "api/inboxes/authenticated_imap.py",
                "api/inboxes/connect-imap.py",
                "api/inboxes/connect-oauth.py",
                "api/inboxes/credentials.py",
                "api/inboxes/oauth-callback.py",
                "api/user/config.py",
            ],
        )
        self.assertTrue((API_DIR / "inboxes" / "fetch-gmail-thread.py").exists())


if __name__ == "__main__":
    unittest.main()
