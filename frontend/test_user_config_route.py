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
                    "message": "A valid beta session is required.",
                },
            },
        )
        write_mock.assert_not_called()


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

    def test_read_error_is_ignored_and_defaults_are_written_once(self):
        read_error = {
            "status": "unavailable",
            "config": None,
            "error": {"code": "user_config_store_unavailable", "message": "read failed"},
        }
        handler, read_mock, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Dark"}}},
            read_result=read_error,
        )
        self.assertEqual(handler.status_code, 200)
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_called_once()
        written = write_mock.call_args.args[2]
        self.assertEqual(written["v"], 1)
        self.assertEqual(written["email"], "owner@example.com")
        self.assertEqual(written["managedInboxes"], [])
        self.assertEqual(written["uiPreferences"], {"themeMode": "Dark"})
        self.assertEqual(handler.payload(), {"ok": True, "config": written})

    def test_merge_sanitization_owner_and_timestamp_behavior_are_unchanged(self):
        existing = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "old",
            "managedInboxes": [],
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
        self.assertEqual(inbox["customImap"]["password"], "")
        self.assertEqual(inbox["customSmtp"]["password"], "")
        self.assertNotIn("oauthAuthorizationUrl", inbox)
        self.assertEqual(handler.payload(), {"ok": True, "config": written})

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
            ["api/inboxes/fetch-gmail-thread.py", "api/user/config.py"],
        )
        self.assertTrue((API_DIR / "inboxes" / "fetch-gmail-thread.py").exists())


if __name__ == "__main__":
    unittest.main()
