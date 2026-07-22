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
VALID_ONBOARDING_SESSION = {
    "schemaVersion": 1,
    "completed": False,
    "currentStep": 2,
    "choices": {
        "primaryRole": "label_owner",
        "internalRole": "label_ar_manager",
        "secondaryRole": "producer",
        "primaryInbox": "custom:vip-mabc123",
        "primaryInboxType": "work",
        "focusPreferences": {
            "demos": "medium",
            "promo": "low",
            "finance": "medium",
            "legal": "low",
            "business": "medium",
            "updates": "low",
            "distribution": "medium",
            "royalties": "low",
            "promoReminders": "medium",
            "paymentReminders": "low",
        },
        "inboxCount": "4+",
        "selectedInboxes": ["main", "custom:vip-mabc123"],
        "customInboxes": [{"id": "custom:vip-mabc123", "name": "VIP requests"}],
    },
}


def onboarding_session(**updates):
    session = json.loads(json.dumps(VALID_ONBOARDING_SESSION))
    session.update(updates)
    return session


class GetRouteTests(unittest.TestCase):
    def invoke(
        self,
        auth=(SESSION_USER, None),
        store=(STORE, None),
        read_result=None,
        read_side_effect=None,
    ):
        if read_result is None:
            read_result = {
                "status": "ok",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "onboardingSession": {},
                },
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
            side_effect=read_side_effect,
        ) as read_mock, patch.object(
            config_route,
            "write_user_config_record",
            return_value={"status": "ok", "record": {"result": "OK"}, "error": None},
        ) as write_mock:
            config_route.handler.do_GET(handler)
        return handler, read_mock, write_mock

    def test_found_record_has_explicit_state_and_preserves_response_headers(self):
        handler, read_mock, write_mock = self.invoke()
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.payload(),
            {
                "ok": True,
                "configState": "found",
                "config": {
                    "v": 1,
                    "email": "owner@example.com",
                    "onboardingSession": {},
                },
            },
        )
        self.assertIn(("Content-Type", "application/json"), handler.response_headers)
        self.assertIn(("Cache-Control", "no-store"), handler.response_headers)
        self.assertIn(
            ("Content-Length", str(len(handler.wfile.getvalue()))),
            handler.response_headers,
        )
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()

    def test_only_a_precise_missing_result_returns_missing_state(self):
        handler, read_mock, write_mock = self.invoke(
            read_result={
                "status": "missing",
                "config": None,
                "error": {"code": "user_config_not_found", "message": "missing"},
            },
        )
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.payload(),
            {"ok": True, "configState": "missing", "config": None},
        )
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()

    def test_unconfigured_store_is_config_unavailable_before_read(self):
        handler, read_mock, write_mock = self.invoke(
            store=(
                None,
                {
                    "code": "user_config_store_unavailable",
                    "message": "private configuration detail",
                },
            ),
        )
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "config_unavailable",
                    "message": "User configuration is temporarily unavailable.",
                },
            },
        )
        self.assertNotIn("private configuration detail", json.dumps(handler.payload()))
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_unavailable_read_or_read_exception_is_config_unavailable(self):
        unavailable = {
            "status": "unavailable",
            "config": None,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "private storage detail",
            },
        }
        cases = (
            {"read_result": unavailable},
            {"read_side_effect": RuntimeError("private exception detail")},
        )
        for invocation in cases:
            with self.subTest(invocation=invocation):
                handler, read_mock, write_mock = self.invoke(**invocation)
                self.assertEqual(handler.status_code, 503)
                self.assertEqual(
                    handler.payload()["error"],
                    {
                        "code": "config_unavailable",
                        "message": "User configuration is temporarily unavailable.",
                    },
                )
                response = json.dumps(handler.payload())
                self.assertNotIn("private storage detail", response)
                self.assertNotIn("private exception detail", response)
                read_mock.assert_called_once_with(STORE, "owner@example.com")
                write_mock.assert_not_called()

    def test_malformed_or_inconsistent_read_results_are_config_invalid(self):
        cases = (
            {
                "status": "malformed",
                "config": None,
                "error": {
                    "code": "user_config_malformed",
                    "message": "private malformed detail",
                },
            },
            {"status": "missing", "config": {"unexpected": True}, "error": None},
            {"status": "ok", "config": None, "error": None},
            {"status": "ok", "config": [], "error": None},
            {"status": "future_status", "config": {}, "error": None},
            {},
            [],
        )
        for read_result in cases:
            with self.subTest(read_result=read_result):
                handler, _, write_mock = self.invoke(read_result=read_result)
                self.assertEqual(handler.status_code, 503)
                self.assertEqual(
                    handler.payload()["error"],
                    {
                        "code": "config_invalid",
                        "message": "User configuration is invalid.",
                    },
                )
                self.assertNotIn("private malformed detail", json.dumps(handler.payload()))
                write_mock.assert_not_called()

    def test_known_corrupt_stored_config_shapes_are_config_invalid_before_sanitization(self):
        invalid_records = {
            "missing onboarding session": {},
            "onboarding session": {"onboardingSession": []},
            "managed inbox collection": {"managedInboxes": {}},
            "managed inbox entry": {"managedInboxes": ["invalid"]},
            "schema version": {"v": True},
            "email": {"email": 1},
            "updated timestamp": {"updatedAt": []},
            "title overrides": {"mailboxTitleOverrides": []},
            "focus overrides": {"mailboxFocusPreferenceOverrides": []},
            "signatures": {"inboxSignatures": []},
            "smart folders": {"smartFolders": {}},
            "primary mailbox id": {"primaryManagedInboxId": 1},
            "ui preferences": {"uiPreferences": []},
            "theme type": {"uiPreferences": {"themeMode": 1}},
            "theme value": {"uiPreferences": {"themeMode": "Sepia"}},
            "ai preference": {"uiPreferences": {"aiSuggestionsEnabled": "yes"}},
            "inbox preference": {"uiPreferences": {"inboxChangesEnabled": 1}},
            "team preference": {"uiPreferences": {"teamActivityEnabled": None}},
            "display overrides": {"displayNameOverrides": []},
            "display override value": {"displayNameOverrides": {"mailbox": 1}},
        }

        for name, stored_config in invalid_records.items():
            with self.subTest(name=name), patch.object(
                config_route,
                "_sanitize_stored_user_config",
            ) as sanitize_mock:
                handler, _, write_mock = self.invoke(
                    read_result={
                        "status": "ok",
                        "config": stored_config,
                        "error": None,
                    },
                )

            self.assertEqual(handler.status_code, 503)
            self.assertEqual(
                handler.payload(),
                {
                    "ok": False,
                    "error": {
                        "code": "config_invalid",
                        "message": "User configuration is invalid.",
                    },
                },
            )
            sanitize_mock.assert_not_called()
            write_mock.assert_not_called()

    def test_valid_known_shapes_and_unknown_top_level_fields_remain_compatible(self):
        stored_config = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "2026-07-21T12:00:00Z",
            "onboardingSession": {},
            "managedInboxes": [{"id": "mailbox-a"}],
            "mailboxTitleOverrides": {},
            "mailboxFocusPreferenceOverrides": {},
            "inboxSignatures": {},
            "smartFolders": [],
            "primaryManagedInboxId": None,
            "uiPreferences": {
                "themeMode": "System",
                "aiSuggestionsEnabled": True,
                "inboxChangesEnabled": False,
                "teamActivityEnabled": True,
                "futurePreference": {"opaque": True},
            },
            "displayNameOverrides": {"mailbox-a": "Main inbox"},
            "futureTopLevelField": {"opaque": True},
        }
        handler, _, write_mock = self.invoke(
            read_result={"status": "ok", "config": stored_config, "error": None},
        )

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.payload()["configState"], "found")
        self.assertEqual(handler.payload()["config"], stored_config)
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

    def test_guest_is_unauthorized_before_storage_io(self):
        guest = {
            "email": "guest@example.com",
            "name": "Guest",
            "userType": "guest",
        }
        handler, read_mock, write_mock = self.invoke(auth=(guest, None))

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
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_legacy_passwords_are_stripped_without_a_get_side_effect(self):
        legacy = {
            "v": 1,
            "email": "owner@example.com",
            "onboardingSession": {},
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
        self.assertEqual(handler.payload()["configState"], "found")
        returned = handler.payload()["config"]
        self.assertNotIn("imap-secret", json.dumps(returned))
        self.assertNotIn("smtp-secret", json.dumps(returned))
        write_mock.assert_not_called()

    def test_configuration_that_cannot_be_safely_normalized_is_config_invalid(self):
        normalization_behaviors = (
            {"side_effect": ValueError("private normalization detail")},
            {"return_value": []},
        )
        for behavior in normalization_behaviors:
            with self.subTest(behavior=behavior), patch.object(
                config_route,
                "_sanitize_stored_user_config",
                **behavior,
            ):
                handler, _, write_mock = self.invoke()

            self.assertEqual(handler.status_code, 503)
            self.assertEqual(
                handler.payload(),
                {
                    "ok": False,
                    "error": {
                        "code": "config_invalid",
                        "message": "User configuration is invalid.",
                    },
                },
            )
            self.assertNotIn("private normalization detail", json.dumps(handler.payload()))
            write_mock.assert_not_called()

    def test_get_returns_the_same_safe_incomplete_v1_session_read_only(self):
        stored_config = {
            "v": 1,
            "email": "owner@example.com",
            "onboardingSession": onboarding_session(),
        }
        handler, read_mock, write_mock = self.invoke(
            read_result={"status": "ok", "config": stored_config, "error": None},
        )

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.payload()["config"]["onboardingSession"],
            VALID_ONBOARDING_SESSION,
        )
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()

    def test_get_rejects_malformed_or_unsafe_stored_v1_sessions(self):
        unsafe_choices = onboarding_session()
        unsafe_choices["choices"]["provider"] = "google"
        malformed_sessions = (
            {"completed": False},
            onboarding_session(currentStep=99),
            onboarding_session(currentStep="2"),
            onboarding_session(choices={"futureChoice": "discard-me"}),
            unsafe_choices,
            {**onboarding_session(), "unexpected": True},
            {
                "completed": True,
                "state": {"inboxConnections": {"main": {"provider": "google"}}},
            },
        )

        for stored_session in malformed_sessions:
            with self.subTest(stored_session=stored_session):
                handler, _, write_mock = self.invoke(
                    read_result={
                        "status": "ok",
                        "config": {"onboardingSession": stored_session},
                        "error": None,
                    },
                )

                self.assertEqual(handler.status_code, 503)
                self.assertEqual(handler.payload()["error"]["code"], "config_invalid")
                self.assertNotIn("config", handler.payload())
                write_mock.assert_not_called()

    def test_get_normalizes_legacy_completed_record_without_connections(self):
        legacy = {
            "completed": True,
            "completedAt": "2026-07-01T12:00:00Z",
            "state": {
                "primaryRole": "producer",
                "focusPreferences": {"demos": "high", "promo": "low"},
            },
        }
        handler, _, write_mock = self.invoke(
            read_result={
                "status": "ok",
                "config": {"onboardingSession": legacy},
                "error": None,
            },
        )

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.payload()["config"]["onboardingSession"],
            {
                "schemaVersion": 1,
                "completed": True,
                "currentStep": 3,
                "choices": {
                    "primaryRole": "producer",
                    "focusPreferences": {"demos": "medium", "promo": "low"},
                },
            },
        )
        write_mock.assert_not_called()

    def test_get_malformed_session_is_config_invalid_and_read_only(self):
        malformed = onboarding_session(currentStep="2")
        handler, read_mock, write_mock = self.invoke(
            read_result={
                "status": "ok",
                "config": {"onboardingSession": malformed},
                "error": None,
            },
        )

        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "config_invalid",
                    "message": "User configuration is invalid.",
                },
            },
        )
        read_mock.assert_called_once_with(STORE, "owner@example.com")
        write_mock.assert_not_called()


class PostRouteTests(unittest.TestCase):
    def invoke(
        self,
        payload,
        *,
        raw_body=None,
        headers=None,
        auth=(SESSION_USER, None),
        store=(STORE, None),
        read_result=None,
        write_result=None,
    ):
        body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
        handler = FakeHandler(body, headers=headers)
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
        for raw_body in (b"{", b"[]", b'\xff'):
            with self.subTest(raw_body=raw_body):
                handler, read_mock, write_mock = self.invoke({}, raw_body=raw_body)
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(handler.payload()["error"]["code"], "invalid_request")
                read_mock.assert_not_called()
                write_mock.assert_not_called()

    def test_body_at_limit_is_accepted_and_declared_oversize_is_413_before_read(self):
        exact_limit_body = b"{}" + b" " * (
            config_route.MAX_USER_CONFIG_BODY_BYTES - 2
        )
        accepted, read_mock, write_mock = self.invoke(
            {},
            raw_body=exact_limit_body,
        )
        self.assertEqual(accepted.status_code, 200)
        read_mock.assert_called_once()
        write_mock.assert_called_once()

        rejected, read_mock, write_mock = self.invoke(
            {},
            raw_body=b"{}",
            headers={
                "content-length": str(config_route.MAX_USER_CONFIG_BODY_BYTES + 1)
            },
        )
        self.assertEqual(rejected.status_code, 413)
        self.assertEqual(
            rejected.payload()["error"]["code"],
            "request_body_too_large",
        )
        self.assertEqual(rejected.rfile.tell(), 0)
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_invalid_content_length_is_400_before_body_or_storage_io(self):
        for content_length in ("-1", "not-a-number", "1.5", ""):
            with self.subTest(content_length=content_length):
                handler, read_mock, write_mock = self.invoke(
                    {},
                    raw_body=b"{}",
                    headers={"content-length": content_length},
                )
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(
                    handler.payload()["error"]["code"],
                    "invalid_request",
                )
                self.assertEqual(handler.rfile.tell(), 0)
                read_mock.assert_not_called()
                write_mock.assert_not_called()

    def test_json_depth_and_node_budgets_are_400_before_storage_io(self):
        too_deep = {}
        cursor = too_deep
        for _ in range(config_route.MAX_USER_CONFIG_JSON_DEPTH):
            child = {}
            cursor["child"] = child
            cursor = child

        too_many_nodes = {
            "items": [None] * config_route.MAX_USER_CONFIG_JSON_NODES,
        }
        for payload in (too_deep, too_many_nodes):
            with self.subTest(kind="depth" if payload is too_deep else "nodes"):
                handler, read_mock, write_mock = self.invoke(payload)
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(
                    handler.payload()["error"]["code"],
                    "invalid_json_structure",
                )
                read_mock.assert_not_called()
                write_mock.assert_not_called()

    def test_json_loads_recursion_error_is_a_safe_400_without_storage_io(self):
        nesting = 10000
        raw_body = b'{"value":' + b"[" * nesting + b"0" + b"]" * nesting + b"}"
        handler, read_mock, write_mock = self.invoke({}, raw_body=raw_body)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.payload()["error"]["code"], "invalid_request")
        self.assertNotIn("recursion", json.dumps(handler.payload()).lower())
        read_mock.assert_not_called()
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

    def test_guest_is_unauthorized_before_body_and_storage_io(self):
        guest = {
            "email": "guest@example.com",
            "name": "Guest",
            "userType": "guest",
        }
        handler, read_mock, write_mock = self.invoke(
            {"config": {"onboardingSession": onboarding_session()}},
            auth=(guest, None),
        )

        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.rfile.tell(), 0)
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
        read_mock.assert_not_called()
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

    def assert_invalid_onboarding_session(self, session, *, existing=None):
        read_result = (
            {"status": "ok", "config": existing, "error": None}
            if existing is not None
            else None
        )
        handler, _, write_mock = self.invoke(
            {
                "config": {
                    "onboardingSession": session,
                    "uiPreferences": {"themeMode": "Dark"},
                }
            },
            read_result=read_result,
        )
        self.assertEqual(handler.status_code, 400)
        self.assertEqual(
            handler.payload(),
            {
                "ok": False,
                "error": {
                    "code": "invalid_onboarding_session",
                    "message": "Onboarding session is invalid.",
                },
            },
        )
        write_mock.assert_not_called()

    def test_valid_incomplete_v1_session_is_stored_as_one_safe_object(self):
        handler, _, write_mock = self.invoke(
            {"config": {"onboardingSession": onboarding_session()}},
        )

        self.assertEqual(handler.status_code, 200)
        written = write_mock.call_args.args[2]
        self.assertEqual(written["onboardingSession"], VALID_ONBOARDING_SESSION)
        self.assertEqual(
            handler.payload()["config"]["onboardingSession"],
            VALID_ONBOARDING_SESSION,
        )

    def test_incomplete_session_replaces_previous_incomplete_session_whole(self):
        replacement = onboarding_session(
            currentStep=1,
            choices={"primaryRole": "dj", "selectedInboxes": ["demo"]},
        )
        handler, _, write_mock = self.invoke(
            {"config": {"onboardingSession": replacement}},
            read_result={
                "status": "ok",
                "config": {"onboardingSession": onboarding_session()},
                "error": None,
            },
        )

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(write_mock.call_args.args[2]["onboardingSession"], replacement)

    def test_omission_preserves_not_started_and_incomplete_sessions(self):
        for stored_session in ({}, onboarding_session(currentStep=1)):
            with self.subTest(stored_session=stored_session):
                handler, _, write_mock = self.invoke(
                    {"config": {"uiPreferences": {"themeMode": "Dark"}}},
                    read_result={
                        "status": "ok",
                        "config": {"onboardingSession": stored_session},
                        "error": None,
                    },
                )

                self.assertEqual(handler.status_code, 200)
                self.assertEqual(
                    write_mock.call_args.args[2]["onboardingSession"],
                    stored_session,
                )

    def test_current_step_requires_a_non_boolean_integer_in_screen_bounds(self):
        for current_step in (-1, 4, True, 1.5, "1"):
            with self.subTest(current_step=current_step):
                self.assert_invalid_onboarding_session(
                    onboarding_session(currentStep=current_step),
                )

    def test_unknown_choice_and_malformed_safe_values_are_rejected(self):
        invalid_choices = (
            {"futureChoice": True},
            {"primaryRole": "unknown-role"},
            {"primaryInbox": "provider-mailbox-id"},
            {"focusPreferences": {"demos": "high"}},
            {"selectedInboxes": ["main", "main"]},
            {"customInboxes": [{"id": "main", "name": "Main"}]},
        )
        for choices in invalid_choices:
            with self.subTest(choices=choices):
                self.assert_invalid_onboarding_session(
                    onboarding_session(choices=choices),
                )

    def test_custom_inbox_ids_require_the_internal_ascii_slug_namespace(self):
        valid_id = "custom:release-requests-mabc123"
        handler, _, write_mock = self.invoke(
            {
                "config": {
                    "onboardingSession": onboarding_session(
                        choices={
                            "primaryInbox": valid_id,
                            "selectedInboxes": [valid_id],
                            "customInboxes": [
                                {"id": valid_id, "name": "Release requests"}
                            ],
                        },
                    )
                }
            }
        )
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            write_mock.call_args.args[2]["onboardingSession"]["choices"],
            {
                "primaryInbox": valid_id,
                "selectedInboxes": [valid_id],
                "customInboxes": [{"id": valid_id, "name": "Release requests"}],
            },
        )

        invalid_ids = (
            "custom:",
            "custom:-vip-mabc123",
            "custom:vip-mabc123-",
            "custom:vip--mabc123",
            "custom:VIP-mabc123",
            "custom:owner@example.com",
            "custom: access-token=secret",
            "custom:imap://user:password@host",
            "custom:provider:google",
        )
        for inbox_id in invalid_ids:
            with self.subTest(inbox_id=inbox_id):
                self.assert_invalid_onboarding_session(
                    onboarding_session(choices={"primaryInbox": inbox_id}),
                )

    def test_deeply_nested_attacker_session_is_a_400_without_storage_io(self):
        deeply_nested = "attacker-controlled"
        for _ in range(400):
            deeply_nested = [deeply_nested]

        handler, read_mock, write_mock = self.invoke(
            {
                "config": {
                    "onboardingSession": onboarding_session(
                        choices={"futureChoice": deeply_nested},
                    ),
                    "uiPreferences": {"themeMode": "Dark"},
                }
            }
        )

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(
            handler.payload()["error"]["code"],
            "invalid_json_structure",
        )
        read_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_credential_connection_and_provider_authority_keys_are_rejected(self):
        forbidden_fields = (
            "inboxConnections",
            "provider",
            "email",
            "host",
            "username",
            "password",
            "token",
            "accessToken",
            "refreshToken",
            "credentialRef",
            "ciphertext",
            "oauthAuthorizationUrl",
            "oauthCode",
            "oauthState",
            "imapPort",
            "smtpSecurity",
            "connectedStatus",
            "reconnectStatus",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                self.assert_invalid_onboarding_session(
                    onboarding_session(choices={field: "attacker-controlled"}),
                )

        nested_secret = onboarding_session()
        nested_secret["choices"]["customInboxes"][0]["password"] = "private"
        self.assert_invalid_onboarding_session(nested_secret)

    def test_user_workspace_owner_and_auth_session_keys_are_rejected(self):
        for field in ("userId", "workspaceId", "owner-email", "auth0Data", "session"):
            with self.subTest(field=field):
                self.assert_invalid_onboarding_session(
                    onboarding_session(choices={field: "attacker-controlled"}),
                )

    def test_completed_true_is_rejected_from_an_ordinary_config_write(self):
        self.assert_invalid_onboarding_session(
            onboarding_session(completed=True),
        )

    def test_existing_completed_true_cannot_be_reset_or_cleared(self):
        existing = {
            "onboardingSession": {
                "completed": True,
                "state": {"primaryRole": "producer"},
            },
        }
        for session in (onboarding_session(), {}):
            with self.subTest(session=session):
                self.assert_invalid_onboarding_session(session, existing=existing)

    def test_unrelated_update_preserves_raw_legacy_completed_session(self):
        legacy_session = {
            "completed": True,
            "completedAt": "2026-07-01T12:00:00Z",
            "state": {
                "primaryRole": "producer",
                "internalRole": "management",
                "focusPreferences": {"demos": "high"},
            },
        }
        existing = {"onboardingSession": legacy_session}
        handler, _, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Dark"}}},
            read_result={"status": "ok", "config": existing, "error": None},
        )

        self.assertEqual(handler.status_code, 200)
        written = write_mock.call_args.args[2]
        self.assertEqual(written["onboardingSession"], legacy_session)
        self.assertEqual(written["uiPreferences"], {"themeMode": "Dark"})
        self.assertEqual(
            handler.payload()["config"]["onboardingSession"],
            {
                "schemaVersion": 1,
                "completed": True,
                "currentStep": 3,
                "choices": {
                    "primaryRole": "producer",
                    "internalRole": "management",
                    "focusPreferences": {"demos": "medium"},
                },
            },
        )

    def test_unrelated_update_preserves_raw_v1_completed_session(self):
        completed_session = onboarding_session(completed=True, currentStep=3)
        existing = {"onboardingSession": completed_session}
        handler, _, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Light"}}},
            read_result={"status": "ok", "config": existing, "error": None},
        )

        self.assertEqual(handler.status_code, 200)
        written_session = write_mock.call_args.args[2]["onboardingSession"]
        self.assertEqual(written_session, completed_session)
        self.assertEqual(
            handler.payload()["config"]["onboardingSession"],
            completed_session,
        )

    def test_malformed_stored_sessions_fail_closed_on_post_without_write(self):
        malformed_completed = {
            "completed": True,
            "state": {"primaryRole": "producer"},
            "unexpected": True,
        }
        malformed_incomplete = onboarding_session(currentStep="2")

        for stored_session in (malformed_completed, malformed_incomplete):
            with self.subTest(stored_session=stored_session):
                handler, read_mock, write_mock = self.invoke(
                    {"config": {"uiPreferences": {"themeMode": "Dark"}}},
                    read_result={
                        "status": "ok",
                        "config": {"onboardingSession": stored_session},
                        "error": None,
                    },
                )

                self.assertEqual(handler.status_code, 503)
                self.assertEqual(
                    handler.payload(),
                    {
                        "ok": False,
                        "error": {
                            "code": "config_invalid",
                            "message": "User configuration is invalid.",
                        },
                    },
                )
                read_mock.assert_called_once_with(STORE, "owner@example.com")
                write_mock.assert_not_called()

    def test_malformed_session_rejects_entire_config_without_partial_write(self):
        malformed_sessions = (
            None,
            [],
            {"schemaVersion": 1, "completed": False, "currentStep": 1},
            onboarding_session(schemaVersion=2),
            onboarding_session(choices=[]),
            {**onboarding_session(), "unexpected": True},
        )
        for session in malformed_sessions:
            with self.subTest(session=session):
                self.assert_invalid_onboarding_session(session)

    def test_empty_session_remains_valid_not_started_state(self):
        handler, _, write_mock = self.invoke(
            {"config": {"onboardingSession": {}}},
        )

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(write_mock.call_args.args[2]["onboardingSession"], {})
        self.assertEqual(handler.payload()["config"]["onboardingSession"], {})

    def test_merge_sanitization_owner_and_timestamp_behavior_are_unchanged(self):
        existing = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "old",
            "onboardingSession": {},
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
                "onboardingSession": {},
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

    def assert_no_store_detail(
        self,
        request,
        *details,
        error_code="user_config_store_unavailable",
    ):
        response_text = json.dumps(request.payload())
        self.assertEqual(request.status_code, 503)
        self.assertEqual(
            request.payload()["error"]["code"],
            error_code,
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
            error_code="config_invalid",
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
        self.assert_no_store_detail(
            request,
            "unexpected-record",
            "raw future detail",
            error_code="config_invalid",
        )
        write_mock.assert_not_called()

    def test_invalid_ok_payload_fails_closed_without_writing(self):
        for invalid_payload, raw_marker in ((None, "null"), ([], "[]")):
            with self.subTest(invalid_payload=invalid_payload):
                request, _, write_mock = self.invoke(
                    {"config": {"managedInboxes": []}},
                    {"status": "ok", "config": invalid_payload},
                )
                self.assertEqual(request.status_code, 503)
                self.assertEqual(
                    request.payload(),
                    {
                        "ok": False,
                        "error": {
                            "code": "config_invalid",
                            "message": "User configuration is invalid.",
                        },
                    },
                )
                self.assertNotIn(raw_marker, json.dumps(request.payload()))
                self.assertNotIn("config", request.payload())
                write_mock.assert_not_called()

    def test_inconsistent_missing_payload_is_config_invalid_without_writing(self):
        request, _, write_mock = self.invoke(
            {"config": {"uiPreferences": {"themeMode": "Dark"}}},
            {
                "status": "missing",
                "config": {"onboardingSession": {}},
                "error": None,
            },
        )

        self.assertEqual(request.status_code, 503)
        self.assertEqual(request.payload()["error"]["code"], "config_invalid")
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
                    "onboardingSession": {},
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
                    "onboardingSession": {},
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
                    "onboardingSession": {},
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
                    "onboardingSession": {},
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
                    "onboardingSession": {},
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
                    "onboardingSession": {},
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
