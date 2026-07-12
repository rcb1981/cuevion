import importlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

FRONTEND_DIR = Path(__file__).resolve().parent
API_DIR = FRONTEND_DIR / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import beta_auth
import user_config_store


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def cookie_headers(token: str | None = None):
    return {"cookie": f"cuevion_beta_session={token}" if token else ""}


def managed_inbox(mailbox_id="mailbox-a", **overrides):
    return {
        "id": mailbox_id,
        "email": "artist@example.com",
        "provider": "google",
        "connected": True,
        "connectionStatus": "connected",
        "customImap": {"password": "must-not-return"},
        **overrides,
    }


class AuthenticationTests(unittest.TestCase):
    def test_missing_cookie_and_missing_secret_are_distinct(self):
        with patch.dict(os.environ, {"CUEVION_BETA_SESSION_SECRET": "secret"}, clear=False):
            user, error = user_config_store.resolve_authenticated_user({})
        self.assertIsNone(user)
        self.assertEqual(error["code"], "missing_session")

        with patch.dict(os.environ, {}, clear=True):
            user, error = user_config_store.resolve_authenticated_user(cookie_headers("value"))
        self.assertIsNone(user)
        self.assertEqual(error["code"], "session_auth_unavailable")

    def test_invalid_expired_and_bad_signature_tokens_are_rejected(self):
        with patch.dict(os.environ, {"CUEVION_BETA_SESSION_SECRET": "secret"}, clear=False):
            _, malformed_error = user_config_store.resolve_authenticated_user(
                cookie_headers("malformed"),
            )
            with patch.object(beta_auth.time, "time", return_value=100):
                expired_token = beta_auth.build_beta_session_token(
                    name="User",
                    email="user@example.com",
                )
            with patch.object(
                beta_auth.time,
                "time",
                return_value=100 + beta_auth.DEFAULT_BETA_SESSION_TTL_SECONDS + 1,
            ):
                _, expired_error = user_config_store.resolve_authenticated_user(
                    cookie_headers(expired_token),
                )
            encoded_payload, _ = expired_token.split(".", 1)
            _, signature_error = user_config_store.resolve_authenticated_user(
                cookie_headers(f"{encoded_payload}.invalid"),
            )

        self.assertEqual(malformed_error["code"], "invalid_session")
        self.assertEqual(expired_error["code"], "invalid_session")
        self.assertEqual(signature_error["code"], "invalid_session")

    def test_valid_session_returns_normalized_context_without_token(self):
        with patch.dict(os.environ, {"CUEVION_BETA_SESSION_SECRET": "secret"}, clear=False):
            token = beta_auth.build_beta_session_token(
                name="  User Name  ",
                email="USER@EXAMPLE.COM",
            )
            user, error = user_config_store.resolve_authenticated_user(cookie_headers(token))

        self.assertIsNone(error)
        self.assertEqual(
            user,
            {"email": "user@example.com", "name": "User Name", "userType": "member"},
        )
        self.assertNotIn(token, repr(user))


class StoreTests(unittest.TestCase):
    store = {"rest_url": "https://kv.example", "rest_token": "kv-secret"}

    def test_store_resolution_requires_both_variables(self):
        with patch.dict(
            os.environ,
            {"KV_REST_API_URL": "https://kv.example/", "KV_REST_API_TOKEN": "token"},
            clear=True,
        ):
            store, error = user_config_store.resolve_user_config_store()
        self.assertIsNone(error)
        self.assertEqual(store, {"rest_url": "https://kv.example", "rest_token": "token"})

        for environment in (
            {},
            {"KV_REST_API_URL": "https://kv.example"},
            {"KV_REST_API_TOKEN": "token"},
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ,
                environment,
                clear=True,
            ):
                store, error = user_config_store.resolve_user_config_store()
                self.assertIsNone(store)
                self.assertEqual(error["code"], "user_config_store_unavailable")

    def test_exact_key_and_object_or_string_record_reads(self):
        self.assertEqual(
            user_config_store.build_user_config_key(" USER@Example.COM "),
            "cuevion:user:v1:user@example.com",
        )

        for provider_result in (
            {"v": 1, "managedInboxes": []},
            json.dumps({"v": 1, "managedInboxes": []}),
        ):
            captured = []

            def fake_urlopen(request, timeout):
                captured.append((request, timeout))
                return FakeResponse(json.dumps({"result": provider_result}))

            with self.subTest(provider_result=provider_result), patch.object(
                user_config_store,
                "urlopen",
                side_effect=fake_urlopen,
            ):
                result = user_config_store.read_user_config_record(
                    self.store,
                    "USER@example.com",
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["config"], {"v": 1, "managedInboxes": []})
            self.assertEqual(len(captured), 1)
            self.assertTrue(
                all(captured_request.get_method() != "POST" for captured_request, _ in captured),
            )
            request, timeout = captured[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(
                request.full_url,
                "https://kv.example/get/cuevion%3Auser%3Av1%3Auser%40example.com",
            )
            self.assertEqual(timeout, 20)
            self.assertIsNone(request.data)

    def test_missing_and_malformed_records_retain_precise_status(self):
        cases = [
            ({"result": None}, "missing"),
            ({"result": "not-json"}, "malformed"),
            ({"result": ["not", "a", "record"]}, "malformed"),
            (["not", "a", "provider-response"], "malformed"),
        ]
        for payload, expected_status in cases:
            with self.subTest(payload=payload), patch.object(
                user_config_store,
                "urlopen",
                return_value=FakeResponse(json.dumps(payload)),
            ):
                result = user_config_store.read_user_config_record(
                    self.store,
                    "user@example.com",
                )
            self.assertEqual(result["status"], expected_status)

    def test_http_and_network_failures_are_unavailable(self):
        failures = [
            HTTPError(
                "https://kv.example",
                503,
                "Unavailable",
                {},
                io.BytesIO(b'{"message":"Store unavailable"}'),
            ),
            URLError("offline"),
        ]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(
                user_config_store,
                "urlopen",
                side_effect=failure,
            ):
                result = user_config_store.read_user_config_record(
                    self.store,
                    "user@example.com",
                )
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["error"]["code"], "user_config_store_unavailable")

    def test_write_uses_exact_rest_contract_without_mutating_record(self):
        captured = []
        record = {"z": 2, "a": {"value": 1}}
        original = json.loads(json.dumps(record))

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse('{"result":"OK"}')

        with patch.object(user_config_store, "urlopen", side_effect=fake_urlopen):
            result = user_config_store.write_user_config_record(
                self.store,
                "user@example.com",
                record,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(record, original)
        request, timeout = captured[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.full_url,
            "https://kv.example/set/cuevion%3Auser%3Av1%3Auser%40example.com",
        )
        self.assertEqual(request.data, b'{"a":{"value":1},"z":2}')
        self.assertEqual(request.get_header("Authorization"), "Bearer kv-secret")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(timeout, 20)

    def test_module_import_has_no_network_or_store_activity(self):
        with patch("urllib.request.urlopen") as request_mock:
            importlib.reload(user_config_store)
            request_mock.assert_not_called()
        importlib.reload(user_config_store)


class AuthenticatedReadTests(unittest.TestCase):
    def test_authenticated_read_uses_only_session_email_and_retains_status(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        store = {"rest_url": "https://kv.example", "rest_token": "token"}
        missing = {
            "status": "missing",
            "config": None,
            "error": {"code": "user_config_not_found", "message": "missing"},
        }
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=(store, None),
        ), patch.object(
            user_config_store,
            "read_user_config_record",
            return_value=missing,
        ) as read_mock:
            resolved_user, result = user_config_store.read_user_config_for_authenticated_user(
                {"untrusted": "request identity is ignored"},
            )

        self.assertEqual(resolved_user, user)
        self.assertEqual(result["status"], "missing")
        read_mock.assert_called_once_with(store, "owner@example.com")

    def test_store_failure_remains_unavailable(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        error = {"code": "user_config_store_unavailable", "message": "unavailable"}
        with patch.object(
            user_config_store,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            user_config_store,
            "resolve_user_config_store",
            return_value=(None, error),
        ):
            _, result = user_config_store.read_user_config_for_authenticated_user({})
        self.assertEqual(result["status"], "unavailable")


class ManagedInboxTests(unittest.TestCase):
    def test_exact_provider_agnostic_disconnected_inbox_returns_minimal_copy(self):
        source = managed_inbox(
            provider="custom_imap",
            connected=False,
            connectionStatus="connection_failed",
        )
        config = {"managedInboxes": [source]}
        result = user_config_store.resolve_managed_inbox(config, "mailbox-a")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["inbox"],
            {
                "id": "mailbox-a",
                "email": "artist@example.com",
                "provider": "custom_imap",
                "connected": False,
                "connectionStatus": "connection_failed",
            },
        )
        result["inbox"]["email"] = "changed@example.com"
        self.assertEqual(source["email"], "artist@example.com")

    def test_invalid_requested_ids_and_case_mismatch(self):
        config = {"managedInboxes": [managed_inbox()]}
        for mailbox_id in ("", "   ", " mailbox-a", "mailbox-a "):
            with self.subTest(mailbox_id=mailbox_id):
                result = user_config_store.resolve_managed_inbox(config, mailbox_id)
                self.assertEqual(result["error"]["code"], "invalid_mailbox_id")

        result = user_config_store.resolve_managed_inbox(config, "MAILBOX-A")
        self.assertEqual(result["status"], "not_found")

    def test_missing_malformed_and_duplicate_inboxes_fail_closed(self):
        cases = [
            ({}, "missing", "managed_inbox_malformed"),
            ({"managedInboxes": {}}, "missing", "managed_inbox_malformed"),
            ({"managedInboxes": ["bad"]}, "missing", "managed_inbox_malformed"),
            ({"managedInboxes": [{}]}, "missing", "managed_inbox_malformed"),
            (
                {"managedInboxes": [managed_inbox(" mailbox-a")]},
                "missing",
                "managed_inbox_malformed",
            ),
            (
                {"managedInboxes": [managed_inbox(), managed_inbox()]},
                "mailbox-a",
                "duplicate_mailbox_id",
            ),
            (
                {"managedInboxes": [managed_inbox()]},
                "missing",
                "managed_inbox_not_found",
            ),
        ]
        for config, mailbox_id, error_code in cases:
            with self.subTest(config=config):
                result = user_config_store.resolve_managed_inbox(config, mailbox_id)
            self.assertEqual(result["error"]["code"], error_code)

    def test_minimal_fields_are_validated(self):
        invalid_overrides = [
            {"email": None},
            {"provider": 1},
            {"connected": 1},
            {"connectionStatus": None},
        ]
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                result = user_config_store.resolve_managed_inbox(
                    {"managedInboxes": [managed_inbox(**overrides)]},
                    "mailbox-a",
                )
            self.assertEqual(result["error"]["code"], "managed_inbox_malformed")

    def test_owner_match_legacy_missing_owner_and_conflict(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}

        def run(config):
            read_result = {"status": "ok", "config": config, "error": None}
            with patch.object(
                user_config_store,
                "read_user_config_for_authenticated_user",
                return_value=(user, read_result),
            ):
                return user_config_store.resolve_owned_managed_inbox({}, "mailbox-a")

        self.assertEqual(
            run({"email": "OWNER@example.com", "managedInboxes": [managed_inbox()]})[
                "status"
            ],
            "ok",
        )
        self.assertEqual(run({"managedInboxes": [managed_inbox()]})["status"], "ok")
        conflict = run(
            {"email": "other@example.com", "managedInboxes": [managed_inbox()]},
        )
        self.assertEqual(conflict["error"]["code"], "user_config_malformed")

    def test_same_mailbox_id_in_another_users_config_is_inaccessible(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        read_result = {
            "status": "ok",
            "config": {
                "email": "other@example.com",
                "managedInboxes": [managed_inbox()],
            },
            "error": None,
        }
        with patch.object(
            user_config_store,
            "read_user_config_for_authenticated_user",
            return_value=(user, read_result),
        ):
            result = user_config_store.resolve_owned_managed_inbox({}, "mailbox-a")
        self.assertEqual(result["status"], "malformed")
        self.assertIsNone(result["inbox"])


if __name__ == "__main__":
    unittest.main()
