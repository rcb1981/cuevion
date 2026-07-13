import base64
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import mailbox_secret_store as store


def load_route():
    spec = importlib.util.spec_from_file_location(
        "mailbox_secret_v1_cleanup_route_test",
        CURRENT_DIR / "cleanup-mailbox-secret-v1.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cleanup_route = load_route()


def encoded_key(byte=b"k"):
    return base64.urlsafe_b64encode(byte * 32).decode().rstrip("=")


class FakeHandler:
    def __init__(self, body=b"{}", headers=None):
        self.headers = {"content-length": str(len(body)), **(headers or {})}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code = None
        self.response_headers = []

    def send_response(self, status_code):
        self.status_code = status_code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def response(self):
        if not self.wfile.getvalue():
            return None
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class InMemorySecretStore:
    def __init__(self):
        self.records = {}
        self.cleanup_deletes = []
        self.cleanup_delete_error = None

    def read(self, _config, key):
        return self.records.get(key), None

    def write(self, _config, key, record):
        self.records[key] = json.loads(json.dumps(record))
        return None, None

    def delete_with_outcome(self, _config, key):
        self.cleanup_deletes.append(key)
        if self.cleanup_delete_error:
            return None, self.cleanup_delete_error
        if key in self.records:
            self.records.pop(key)
            return "deleted", None
        return "already_absent", None


SESSION_USER = {
    "email": "owner@example.com",
    "name": "Owner",
    "userType": "member",
}


def owned_result(
    *,
    provider="custom_imap",
    connected=True,
    connection_status="connected",
    use_same_credentials=False,
    include_use_same=True,
):
    custom_smtp = {}
    if include_use_same:
        custom_smtp["useSameCredentials"] = use_same_credentials
    return {
        "status": "ok",
        "user": dict(SESSION_USER),
        "inbox": {
            "id": "demo",
            "email": "demo@example.com",
            "provider": provider,
            "connected": connected,
            "connectionStatus": connection_status,
            "customSmtp": custom_smtp,
        },
        "config": {"managedInboxes": []},
        "error": None,
    }


class MailboxSecretV1CleanupRouteTests(unittest.TestCase):
    def setUp(self):
        self.memory = InMemorySecretStore()
        self.patches = [
            patch.object(
                store,
                "_resolve_durable_store_config",
                return_value={"configured": True},
            ),
            patch.object(store, "_read_durable_record", side_effect=self.memory.read),
            patch.object(store, "_write_durable_record", side_effect=self.memory.write),
            patch.object(
                store,
                "_delete_durable_record_with_outcome",
                side_effect=self.memory.delete_with_outcome,
            ),
            patch.dict(
                os.environ,
                {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encoded_key()},
                clear=False,
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def _body(self, payload):
        return json.dumps(payload).encode("utf-8")

    def _invoke_post(
        self,
        *,
        payload=None,
        raw_body=None,
        auth=(SESSION_USER, None),
        owned=None,
    ):
        request_payload = {"mailboxId": "demo"} if payload is None else payload
        body = raw_body if raw_body is not None else self._body(request_payload)
        handler = FakeHandler(body)
        resolved_owned = owned if owned is not None else owned_result()
        with patch.object(
            cleanup_route,
            "resolve_authenticated_user",
            return_value=auth,
        ) as auth_mock, patch.object(
            cleanup_route,
            "resolve_owned_managed_inbox_record",
            return_value=resolved_owned,
        ) as owned_mock, patch.object(
            cleanup_route,
            "cleanup_legacy_mailbox_secret_v1",
            wraps=store.cleanup_legacy_mailbox_secret_v1,
        ) as cleanup_mock:
            cleanup_route.handler.do_POST(handler)
        return handler, auth_mock, owned_mock, cleanup_mock

    def _save_v2(
        self,
        owner="owner@example.com",
        mailbox_id="demo",
        imap_password="imap-secret",
        smtp_password="smtp-secret",
    ):
        saved, error = store.save_mailbox_secret(
            owner,
            mailbox_id,
            imap_password=imap_password,
            smtp_password=smtp_password,
        )
        self.assertIsNone(error)
        self.assertIsNotNone(saved)
        return store.build_encrypted_mailbox_secret_key(owner, mailbox_id)

    def _put_v1(
        self,
        owner="owner@example.com",
        mailbox_id="demo",
        imap_password="legacy-imap",
        smtp_password="legacy-smtp",
    ):
        key = store.build_mailbox_secret_key(owner, mailbox_id)
        self.memory.records[key] = {
            "v": 1,
            "mailboxId": mailbox_id,
            "updatedAt": "2026-01-01T00:00:00Z",
            "imapPassword": imap_password,
            "smtpPassword": smtp_password,
        }
        return key

    def test_unsupported_methods_are_rejected_consistently(self):
        for method in ("GET", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                handler = FakeHandler()
                getattr(cleanup_route.handler, f"do_{method}")(handler)
                self.assertEqual(handler.status_code, 405)
                self.assertEqual(handler.response()["error"]["code"], "method_not_allowed")

        head_handler = FakeHandler()
        cleanup_route.handler.do_HEAD(head_handler)
        self.assertEqual(head_handler.status_code, 405)
        self.assertIsNone(head_handler.response())

    def test_unauthenticated_post_stops_before_config_or_secret_access(self):
        handler, auth_mock, owned_mock, cleanup_mock = self._invoke_post(
            auth=(None, {"code": "missing_session", "message": "missing"}),
        )

        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.response()["error"]["code"], "unauthorized")
        auth_mock.assert_called_once()
        owned_mock.assert_not_called()
        cleanup_mock.assert_not_called()
        self.assertEqual(self.memory.cleanup_deletes, [])

    def test_invalid_json_and_non_object_bodies_are_rejected(self):
        for raw_body in (b"{", b"[]", b"null", b'"demo"'):
            with self.subTest(raw_body=raw_body):
                handler, auth_mock, owned_mock, cleanup_mock = self._invoke_post(
                    raw_body=raw_body,
                )
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(handler.response()["error"]["code"], "invalid_request")
                auth_mock.assert_not_called()
                owned_mock.assert_not_called()
                cleanup_mock.assert_not_called()

    def test_missing_extra_and_invalid_mailbox_ids_are_rejected(self):
        invalid_payloads = (
            {},
            {"mailboxId": "demo", "owner": "owner@example.com"},
            {"mailboxId": ""},
            {"mailboxId": " demo "},
            {"mailboxId": "draft-demo"},
            {"mailboxId": None},
            {"mailboxId": 1},
            {"mailboxId": True},
            {"mailboxId": ["demo"]},
            {"mailboxId": {"id": "demo"}},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                handler, auth_mock, owned_mock, cleanup_mock = self._invoke_post(
                    payload=payload,
                )
                self.assertEqual(handler.status_code, 400)
                self.assertEqual(handler.response()["error"]["code"], "invalid_request")
                auth_mock.assert_not_called()
                owned_mock.assert_not_called()
                cleanup_mock.assert_not_called()

    def test_not_owned_missing_and_duplicate_mailboxes_fail_before_cleanup(self):
        cases = (
            (
                "another_owner",
                {
                    "status": "not_found",
                    "user": dict(SESSION_USER),
                    "inbox": None,
                    "config": None,
                    "error": {"code": "managed_inbox_not_found", "message": "missing"},
                },
                404,
                "managed_inbox_not_found",
            ),
            (
                "missing",
                {
                    "status": "not_found",
                    "user": dict(SESSION_USER),
                    "inbox": None,
                    "config": None,
                    "error": {"code": "user_config_not_found", "message": "missing"},
                },
                404,
                "managed_inbox_not_found",
            ),
            (
                "duplicate",
                {
                    "status": "malformed",
                    "user": dict(SESSION_USER),
                    "inbox": None,
                    "config": {"managedInboxes": []},
                    "error": {"code": "duplicate_mailbox_id", "message": "duplicate"},
                },
                409,
                "duplicate_mailbox_id",
            ),
        )
        for name, result, status_code, error_code in cases:
            with self.subTest(name=name):
                handler, _, _, cleanup_mock = self._invoke_post(owned=result)
                self.assertEqual(handler.status_code, status_code)
                self.assertEqual(handler.response()["error"]["code"], error_code)
                cleanup_mock.assert_not_called()

    def test_user_config_outage_and_resolver_exception_fail_before_cleanup(self):
        unavailable = {
            "status": "unavailable",
            "user": dict(SESSION_USER),
            "inbox": None,
            "config": None,
            "error": {
                "code": "user_config_store_unavailable",
                "message": "offline",
            },
        }
        handler, _, _, cleanup_mock = self._invoke_post(owned=unavailable)
        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_configuration_unavailable",
        )
        cleanup_mock.assert_not_called()

        body = self._body({"mailboxId": "demo"})
        exception_handler = FakeHandler(body)
        with patch.object(
            cleanup_route,
            "resolve_authenticated_user",
            return_value=(SESSION_USER, None),
        ), patch.object(
            cleanup_route,
            "resolve_owned_managed_inbox_record",
            side_effect=RuntimeError("offline"),
        ), patch.object(
            cleanup_route,
            "cleanup_legacy_mailbox_secret_v1",
        ) as exception_cleanup:
            cleanup_route.handler.do_POST(exception_handler)
        self.assertEqual(exception_handler.status_code, 503)
        self.assertEqual(
            exception_handler.response()["error"]["code"],
            "mailbox_configuration_unavailable",
        )
        exception_cleanup.assert_not_called()

    def test_gmail_and_other_non_custom_providers_are_rejected(self):
        for provider in ("google", "outlook", None):
            with self.subTest(provider=provider):
                handler, _, _, cleanup_mock = self._invoke_post(
                    owned=owned_result(provider=provider),
                )
                self.assertEqual(handler.status_code, 409)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "invalid_mailbox_provider",
                )
                cleanup_mock.assert_not_called()

    def test_disconnected_and_invalid_connection_status_are_rejected(self):
        for connected, connection_status in (
            (False, "disconnected"),
            (True, "reconnect_required"),
            (True, None),
        ):
            with self.subTest(
                connected=connected,
                connection_status=connection_status,
            ):
                handler, _, _, cleanup_mock = self._invoke_post(
                    owned=owned_result(
                        connected=connected,
                        connection_status=connection_status,
                    ),
                )
                self.assertEqual(handler.status_code, 409)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "mailbox_not_connected",
                )
                cleanup_mock.assert_not_called()

    def test_missing_and_non_boolean_use_same_credentials_are_rejected(self):
        configurations = (
            owned_result(include_use_same=False),
            owned_result(use_same_credentials=None),
            owned_result(use_same_credentials=1),
            owned_result(use_same_credentials="true"),
        )
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                handler, _, _, cleanup_mock = self._invoke_post(
                    owned=configuration,
                )
                self.assertEqual(handler.status_code, 500)
                self.assertEqual(
                    handler.response()["error"]["code"],
                    "mailbox_configuration_malformed",
                )
                cleanup_mock.assert_not_called()

    def test_missing_and_malformed_v2_leave_v1_untouched(self):
        legacy_key = self._put_v1()

        missing, _, _, _ = self._invoke_post()
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.response()["error"]["code"], "mailbox_secret_v2_missing")
        self.assertIn(legacy_key, self.memory.records)

        encrypted_key = store.build_encrypted_mailbox_secret_key(
            "owner@example.com",
            "demo",
        )
        self.memory.records[encrypted_key] = {"v": 2, "ciphertext": "invalid"}
        malformed, _, _, _ = self._invoke_post()
        self.assertEqual(malformed.status_code, 500)
        self.assertEqual(
            malformed.response()["error"]["code"],
            "mailbox_secret_v2_malformed",
        )
        self.assertIn(legacy_key, self.memory.records)

    def test_wrong_key_leaves_v1_and_v2_untouched(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))

        with patch.dict(
            os.environ,
            {store.MAILBOX_SECRET_ENCRYPTION_KEY_ENV: encoded_key(b"j")},
            clear=True,
        ):
            handler, _, _, _ = self._invoke_post()

        self.assertEqual(handler.status_code, 500)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_secret_v2_decryption_failed",
        )
        self.assertIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)

    def test_corrupt_structurally_valid_ciphertext_is_decryption_failure(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        encrypted_record = self.memory.records[encrypted_key]
        first_character = encrypted_record["ciphertext"][0]
        encrypted_record["ciphertext"] = (
            ("A" if first_character != "A" else "B")
            + encrypted_record["ciphertext"][1:]
        )
        encrypted_before = json.loads(json.dumps(encrypted_record))

        handler, _, _, _ = self._invoke_post()

        self.assertEqual(handler.status_code, 500)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_secret_v2_decryption_failed",
        )
        self.assertIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)
        serialized = json.dumps(handler.response())
        self.assertNotIn(encrypted_before["nonce"], serialized)
        self.assertNotIn(encrypted_before["ciphertext"], serialized)

    def test_valid_cleanup_is_exact_preserves_v2_and_is_idempotent(self):
        encrypted_key = self._save_v2()
        legacy_key = self._put_v1()
        other_encrypted_key = self._save_v2(
            mailbox_id="other",
            imap_password="other-imap",
            smtp_password="other-smtp",
        )
        other_legacy_key = self._put_v1(
            mailbox_id="other",
            imap_password="other-legacy-imap",
            smtp_password="other-legacy-smtp",
        )
        encrypted_before = json.loads(json.dumps(self.memory.records[encrypted_key]))
        other_encrypted_before = json.loads(
            json.dumps(self.memory.records[other_encrypted_key])
        )
        other_legacy_before = json.loads(json.dumps(self.memory.records[other_legacy_key]))

        first, _, _, first_cleanup = self._invoke_post()
        second, _, _, second_cleanup = self._invoke_post()

        self.assertEqual(first.response(), {"ok": True, "status": "deleted"})
        self.assertEqual(second.response(), {"ok": True, "status": "already_absent"})
        first_cleanup.assert_called_once_with("owner@example.com", "demo", False)
        second_cleanup.assert_called_once_with("owner@example.com", "demo", False)
        self.assertNotIn(legacy_key, self.memory.records)
        self.assertEqual(self.memory.records[encrypted_key], encrypted_before)
        self.assertEqual(self.memory.records[other_encrypted_key], other_encrypted_before)
        self.assertEqual(self.memory.records[other_legacy_key], other_legacy_before)

    def test_storage_and_v1_delete_failures_fail_closed(self):
        self._save_v2()
        legacy_key = self._put_v1()
        outage = {
            "code": "mailbox_secret_store_unavailable",
            "message": "offline",
        }

        with patch.object(
            store,
            "_read_durable_record",
            return_value=(None, outage),
        ):
            read_failure, _, _, _ = self._invoke_post()
        self.assertEqual(read_failure.status_code, 503)
        self.assertEqual(
            read_failure.response()["error"]["code"],
            "mailbox_secret_store_unavailable",
        )
        self.assertIn(legacy_key, self.memory.records)

        self.memory.cleanup_delete_error = outage
        delete_failure, _, _, _ = self._invoke_post()
        self.assertEqual(delete_failure.status_code, 503)
        self.assertEqual(
            delete_failure.response()["error"]["code"],
            "mailbox_secret_v1_delete_failed",
        )
        self.assertIn(legacy_key, self.memory.records)

    def test_missing_encryption_configuration_fails_closed(self):
        self._save_v2()
        legacy_key = self._put_v1()

        with patch.dict(os.environ, {}, clear=True):
            handler, _, _, _ = self._invoke_post()

        self.assertEqual(handler.status_code, 503)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_secret_encryption_unavailable",
        )
        self.assertIn(legacy_key, self.memory.records)

    def test_responses_are_secret_free_and_no_external_call_occurs(self):
        encrypted_key = self._save_v2(
            imap_password="response-forbidden-imap",
            smtp_password="response-forbidden-smtp",
        )
        self._put_v1(
            imap_password="response-forbidden-legacy-imap",
            smtp_password="response-forbidden-legacy-smtp",
        )
        encrypted_record = self.memory.records[encrypted_key]

        with patch.object(store, "urlopen") as network_mock:
            handler, _, _, _ = self._invoke_post()

        network_mock.assert_not_called()
        serialized = json.dumps(handler.response())
        for forbidden in (
            "response-forbidden-imap",
            "response-forbidden-smtp",
            "response-forbidden-legacy-imap",
            "response-forbidden-legacy-smtp",
            encrypted_record["nonce"],
            encrypted_record["ciphertext"],
            encoded_key(),
            "owner@example.com",
            "demo@example.com",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
