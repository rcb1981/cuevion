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
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import user_config_store
from api.auth import http as auth_http
from api.auth import runtime as auth_runtime


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")

    def read(self, amount=None):
        return self.payload if amount is None else self.payload[:amount]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


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
    member = auth_runtime.AuthenticatedMemberContext(
        user_id="usr_test",
        email="user@example.com",
        name="User Name",
        workspace_id="wsp_test",
        membership_role="member",
    )

    def test_missing_session_and_authentication_unavailability_are_distinct(self):
        missing = auth_runtime.AuthenticatedMemberResolution(
            auth_runtime.MemberResolutionOutcome.UNAUTHENTICATED,
            None,
        )
        with patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            return_value=missing,
        ):
            user, error = user_config_store.resolve_authenticated_user({})
        self.assertIsNone(user)
        self.assertEqual(error["code"], "missing_session")

        unavailable = auth_runtime.AuthenticatedMemberResolution(
            auth_runtime.MemberResolutionOutcome.UNAVAILABLE,
            None,
        )
        with patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            return_value=unavailable,
        ):
            user, error = user_config_store.resolve_authenticated_user({})
        self.assertIsNone(user)
        self.assertEqual(error["code"], "session_auth_unavailable")

    def test_invalid_session_and_malformed_headers_are_rejected(self):
        invalid = auth_runtime.AuthenticatedMemberResolution(
            auth_runtime.MemberResolutionOutcome.UNAUTHENTICATED,
            None,
            ("clear-session",),
        )
        with patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            return_value=invalid,
        ):
            _, invalid_error = user_config_store.resolve_authenticated_user({})

        with patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            side_effect=auth_http.HttpBoundaryError("invalid_request", 400),
        ):
            _, malformed_error = user_config_store.resolve_authenticated_user({})

        self.assertEqual(invalid_error["code"], "invalid_session")
        self.assertEqual(malformed_error["code"], "invalid_session")

    def test_valid_member_returns_only_canonical_context(self):
        authenticated = auth_runtime.AuthenticatedMemberResolution(
            auth_runtime.MemberResolutionOutcome.AUTHENTICATED,
            self.member,
        )
        with patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            return_value=authenticated,
        ) as resolver:
            user, error = user_config_store.resolve_authenticated_user(
                {"cookie": "__Host-cuevion_session=opaque"}
            )

        self.assertIsNone(error)
        self.assertEqual(
            user,
            {"email": "user@example.com", "name": "User Name", "userType": "member"},
        )
        resolver.assert_called_once_with(
            (("cookie", "__Host-cuevion_session=opaque"),)
        )
        self.assertNotIn("opaque", repr(user))


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
            ({}, "unavailable"),
            ({"result": "not-json"}, "malformed"),
            ({"result": ["not", "a", "record"]}, "malformed"),
            (["not", "a", "provider-response"], "unavailable"),
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

    def test_success_response_boundary_failures_are_bounded_and_sanitized(self):
        failures = [
            FakeResponse(b""),
            FakeResponse(b"{"),
            FakeResponse(b"\xff"),
            FakeResponse(b"x" * (user_config_store.MAX_USER_CONFIG_STORE_RESPONSE_BYTES + 1)),
        ]
        for response in failures:
            with self.subTest(size=len(response.payload)), patch.object(
                user_config_store,
                "urlopen",
                return_value=response,
            ):
                result = user_config_store.read_user_config_record(
                    self.store,
                    "user@example.com",
                )
            self.assertEqual(result["status"], "unavailable")
            serialized = json.dumps(result)
            self.assertNotIn("kv.example", serialized)
            self.assertNotIn("kv-secret", serialized)

        for failure in (TimeoutError("raw timeout"), OSError("raw os detail"), RuntimeError("raw unexpected")):
            with self.subTest(failure=type(failure).__name__), patch.object(
                user_config_store,
                "urlopen",
                side_effect=failure,
            ):
                result = user_config_store.read_user_config_record(
                    self.store,
                    "user@example.com",
                )
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("raw", json.dumps(result))

    def test_write_requires_exact_ok_acknowledgement(self):
        for payload in ({}, {"result": None}, {"result": "STALE"}, []):
            with self.subTest(payload=payload), patch.object(
                user_config_store,
                "urlopen",
                return_value=FakeResponse(json.dumps(payload)),
            ):
                result = user_config_store.write_user_config_record(
                    self.store,
                    "user@example.com",
                    {"v": 1},
                )
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(
                result["error"]["code"],
                "user_config_store_unavailable",
            )

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

    def test_full_owned_resolver_is_separate_and_returns_a_copy(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        source = managed_inbox(
            provider="custom_imap",
            customImap={"host": "imap.example.com", "password": "legacy"},
            customSmtp={"host": "smtp.example.com", "password": "legacy"},
        )
        read_result = {
            "status": "ok",
            "config": {"email": "owner@example.com", "managedInboxes": [source]},
            "error": None,
        }
        with patch.object(
            user_config_store,
            "read_user_config_for_authenticated_user",
            return_value=(user, read_result),
        ):
            result = user_config_store.resolve_owned_managed_inbox_record({}, "mailbox-a")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["inbox"]["customImap"]["host"], "imap.example.com")
        result["inbox"]["customImap"]["host"] = "changed"
        self.assertEqual(source["customImap"]["host"], "imap.example.com")

    def test_owned_upsert_preserves_unrelated_config_and_strips_legacy_passwords(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        existing = {
            "v": 1,
            "email": "owner@example.com",
            "smartFolders": [{"id": "keep"}],
            "managedInboxes": [
                managed_inbox(
                    provider="custom_imap",
                    internalRole="artist_manager",
                    classificationSettings={"keep": True},
                    customImap={"host": "old", "password": "legacy"},
                    customSmtp={"host": "old-smtp", "password": "legacy"},
                )
            ],
        }
        store = {"rest_url": "https://kv.example", "rest_token": "token"}
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
            return_value={"status": "ok", "config": existing, "error": None},
        ), patch.object(
            user_config_store,
            "write_user_config_record",
            return_value={"status": "ok", "record": {}, "error": None},
        ) as write:
            result = user_config_store.upsert_owned_custom_imap_mailbox(
                {},
                "mailbox-a",
                "reconnect",
                {
                    "email": "new@example.com",
                    "customImap": {"host": "imap.new", "port": "993", "ssl": True, "username": "u"},
                    "customSmtp": {"host": "smtp.new", "port": "587", "security": "starttls", "username": "u", "useSameCredentials": True},
                },
            )

        self.assertEqual(result["status"], "ok")
        written = write.call_args.args[2]
        self.assertEqual(written["smartFolders"], [{"id": "keep"}])
        inbox = written["managedInboxes"][0]
        self.assertEqual(inbox["id"], "mailbox-a")
        self.assertEqual(inbox["internalRole"], "artist_manager")
        self.assertEqual(inbox["classificationSettings"], {"keep": True})
        self.assertNotIn("password", inbox["customImap"])
        self.assertNotIn("password", inbox["customSmtp"])

    def test_upsert_initial_rejects_any_existing_id_and_reconnect_requires_custom_imap(self):
        user = {"email": "owner@example.com", "name": "Owner", "userType": "member"}
        store = {"rest_url": "https://kv.example", "rest_token": "token"}
        metadata = {
            "email": "new@example.com",
            "customImap": {"host": "imap.new", "port": "993", "ssl": True, "username": "u"},
            "customSmtp": {"host": "smtp.new", "port": "587", "security": "starttls", "username": "u", "useSameCredentials": True},
        }

        def run(mode, managed_inboxes):
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
                return_value={
                    "status": "ok",
                    "config": {
                        "email": "owner@example.com",
                        "managedInboxes": managed_inboxes,
                    },
                    "error": None,
                },
            ), patch.object(
                user_config_store,
                "write_user_config_record",
            ) as write:
                result = user_config_store.upsert_owned_custom_imap_mailbox(
                    {},
                    "mailbox-a",
                    mode,
                    metadata,
                )
            return result, write

        gmail = managed_inbox(provider="google")
        initial_result, initial_write = run("initial", [gmail])
        self.assertEqual(initial_result["status"], "conflict")
        initial_write.assert_not_called()

        missing_result, missing_write = run("reconnect", [])
        self.assertEqual(missing_result["status"], "not_found")
        missing_write.assert_not_called()

        gmail_result, gmail_write = run("reconnect", [gmail])
        self.assertEqual(gmail_result["status"], "conflict")
        self.assertEqual(
            gmail_result["error"]["code"],
            "managed_inbox_provider_mismatch",
        )
        gmail_write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
