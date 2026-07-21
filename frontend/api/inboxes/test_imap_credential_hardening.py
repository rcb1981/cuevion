import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = API_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import authenticated_imap
import imap_connect_preview


def load_route(filename, name):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


connect_route = load_route("connect-imap.py", "connect_imap_hardening_test")
action_route = load_route("message-action.py", "message_action_hardening_test")
attachment_route = load_route("download-attachment.py", "attachment_hardening_test")
send_route = load_route("send-gmail.py", "send_hardening_test")
credentials_route = load_route("credentials.py", "credentials_hardening_test")


class FakeHandler:
    def __init__(self, payload=None, headers=None):
        body = json.dumps(payload or {}).encode()
        self.headers = {"content-length": str(len(body)), **(headers or {})}
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


def invoke_connect(fake_handler):
    for method_name in ("_send_json", "_handle_credential_connection", "_handle_refresh"):
        setattr(
            fake_handler,
            method_name,
            getattr(connect_route.handler, method_name).__get__(
                fake_handler,
                connect_route.handler,
            ),
        )
    connect_route.handler.do_POST(fake_handler)


def initial_payload(mailbox_id="demo", mode="initial"):
    return {
        "mode": mode,
        "mailboxId": mailbox_id,
        "connection": {
            "provider": "custom_imap",
            "email": "demo@example.com",
            "imap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": True,
                "username": "demo@example.com",
                "password": "one-time-imap",
            },
            "smtp": {
                "host": "smtp.example.com",
                "port": "587",
                "security": "starttls",
                "username": "",
                "password": "",
                "useSameCredentials": True,
            },
        },
        "limit": 20,
    }


def missing_connection_target():
    return {
        "status": "not_found",
        "user": {"email": "owner@example.com"},
        "inbox": None,
        "config": {"managedInboxes": []},
        "error": None,
    }


def existing_connection_target(provider="custom_imap"):
    return {
        "status": "ok",
        "user": {"email": "owner@example.com"},
        "inbox": {"id": "demo", "provider": provider},
        "config": {"managedInboxes": [{"id": "demo", "provider": provider}]},
        "error": None,
    }


def resolved_mailbox():
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
            "smtp": {
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "smtp-user",
                "password": "smtp-secret",
                "useSameCredentials": False,
            },
        },
        "error": None,
    }


class ResolverTests(unittest.TestCase):
    def test_owned_mailbox_metadata_and_secret_are_derived_server_side(self):
        owned = {
            "status": "ok",
            "user": {"email": "owner@example.com", "name": "Owner", "userType": "member"},
            "config": {},
            "inbox": {
                "id": "demo",
                "email": "demo@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionStatus": "connected",
                "customImap": {
                    "host": "imap.example.com",
                    "port": "993",
                    "ssl": True,
                    "username": "imap-user",
                },
                "customSmtp": {
                    "host": "smtp.example.com",
                    "port": "587",
                    "security": "starttls",
                    "username": "smtp-user",
                    "useSameCredentials": False,
                },
            },
            "error": None,
        }
        with patch.object(
            authenticated_imap,
            "resolve_owned_managed_inbox_record",
            return_value=owned,
        ), patch.object(
            authenticated_imap,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {"imapPassword": "imap-secret", "smtpPassword": "smtp-secret"},
                "error": None,
            },
        ):
            result = authenticated_imap.resolve_authenticated_imap_mailbox(
                {"untrusted": "ignored"},
                "demo",
                require_smtp=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mailbox"]["imap"]["host"], "imap.example.com")
        self.assertEqual(result["mailbox"]["smtp"]["password"], "smtp-secret")

    def test_missing_secret_is_reconnect_but_outage_is_503(self):
        owned = {
            "status": "ok",
            "user": {"email": "owner@example.com"},
            "config": {},
            "inbox": {
                "id": "demo",
                "email": "demo@example.com",
                "provider": "custom_imap",
                "connected": True,
                "connectionStatus": "connected",
                "customImap": {"host": "imap.example.com", "port": "993", "ssl": True, "username": "u"},
                "customSmtp": {},
            },
            "error": None,
        }
        with patch.object(authenticated_imap, "resolve_owned_managed_inbox_record", return_value=owned):
            for secret_status, expected_status, expected_http in (
                ("missing", "reconnect_required", 409),
                ("unavailable", "service_unavailable", 503),
                ("malformed", "malformed", 500),
            ):
                with self.subTest(secret_status=secret_status), patch.object(
                    authenticated_imap,
                    "read_mailbox_secret",
                    return_value={"status": secret_status, "record": None, "error": None},
                ):
                    result = authenticated_imap.resolve_authenticated_imap_mailbox({}, "demo")
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["error"]["status_code"], expected_http)


class InitialAndRefreshTests(unittest.TestCase):
    def test_refresh_status_gate_truth_table_stops_before_secrets_and_provider(self):
        missing = object()
        connected_values = (missing, False, True, 1)
        statuses = (
            missing,
            "not_connected",
            "oauth_required",
            "waiting_for_authentication",
            "authenticated_pending_activation",
            "connected",
            "connection_failed",
            "reconnect_required",
            "CONNECTED",
            "connected ",
        )

        for connected in connected_values:
            for connection_status in statuses:
                should_connect = connected is True and connection_status == "connected"
                with self.subTest(
                    connected=("missing" if connected is missing else repr(connected)),
                    connection_status=(
                        "missing"
                        if connection_status is missing
                        else repr(connection_status)
                    ),
                ):
                    handler = FakeHandler(
                        {
                            "mode": "refresh",
                            "mailboxId": "demo",
                            "limit": 20,
                        }
                    )
                    inbox = {
                        "id": "demo",
                        "email": "demo@example.com",
                        "provider": "custom_imap",
                        "customImap": {
                            "host": "imap.example.com",
                            "port": "993",
                            "ssl": True,
                            "username": "imap-user",
                        },
                        "customSmtp": {
                            "host": "smtp.example.com",
                            "port": "587",
                            "security": "starttls",
                            "username": "smtp-user",
                            "useSameCredentials": False,
                        },
                    }
                    if connected is not missing:
                        inbox["connected"] = connected
                    if connection_status is not missing:
                        inbox["connectionStatus"] = connection_status

                    owned = {
                        "status": "ok",
                        "user": {"email": "owner@example.com"},
                        "config": {},
                        "inbox": inbox,
                        "error": None,
                    }
                    with patch.object(
                        connect_route,
                        "resolve_authenticated_user",
                        return_value=({"email": "owner@example.com"}, None),
                    ), patch.object(
                        authenticated_imap,
                        "resolve_owned_managed_inbox_record",
                        return_value=owned,
                    ), patch.object(
                        authenticated_imap,
                        "read_mailbox_secret",
                        return_value={
                            "status": "present",
                            "record": {
                                "imapPassword": "imap-secret",
                                "smtpPassword": "smtp-secret",
                            },
                            "error": None,
                        },
                    ) as secret_lookup, patch.object(
                        imap_connect_preview,
                        "build_connect_preview_response",
                        return_value=(200, {"ok": True, "messages": []}),
                    ) as provider_fetch, patch.object(
                        imap_connect_preview,
                        "open_mailbox_connection",
                    ) as provider_connection:
                        invoke_connect(handler)

                    provider_connection.assert_not_called()
                    if should_connect:
                        self.assertEqual(handler.status, 200)
                        secret_lookup.assert_called_once_with(
                            "owner@example.com",
                            "demo",
                        )
                        provider_fetch.assert_called_once()
                    else:
                        self.assertEqual(handler.status, 409)
                        self.assertEqual(
                            handler.response()["error"]["code"],
                            "reconnect_required",
                        )
                        secret_lookup.assert_not_called()
                        provider_fetch.assert_not_called()

    def test_unauthenticated_initial_stops_before_imap_and_storage(self):
        handler = FakeHandler(initial_payload())
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(None, {})), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
        ) as preview, patch.object(connect_route, "save_mailbox_secret") as save:
            invoke_connect(handler)
        self.assertEqual(handler.status, 401)
        preview.assert_not_called()
        save.assert_not_called()

    def test_unstable_id_and_failed_secret_save_never_persist_connected_config(self):
        user = {"email": "owner@example.com"}
        draft_handler = FakeHandler(initial_payload("draft-1"))
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(user, None)):
            invoke_connect(draft_handler)
        self.assertEqual(draft_handler.status, 400)

        handler = FakeHandler(initial_payload())
        with patch.object(connect_route, "resolve_authenticated_user", return_value=(user, None)), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=missing_connection_target(),
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=(None, {"code": "unavailable"}),
        ), patch.object(connect_route, "upsert_owned_custom_imap_mailbox") as upsert:
            invoke_connect(handler)
        self.assertEqual(handler.status, 503)
        self.assertNotIn("one-time-imap", json.dumps(handler.response()))
        upsert.assert_not_called()

    def test_refresh_rejects_browser_identity_before_resolving(self):
        handler = FakeHandler(
            {"mode": "refresh", "mailboxId": "demo", "host": "evil.example", "password": "x"}
        )
        with patch.object(connect_route, "resolve_authenticated_user", return_value=({"email": "owner@example.com"}, None)), patch.object(
            connect_route,
            "resolve_authenticated_imap_mailbox",
        ) as resolver:
            invoke_connect(handler)
        self.assertEqual(handler.status, 400)
        resolver.assert_not_called()

    def test_initial_rejects_any_existing_id_before_provider_or_storage(self):
        user = {"email": "owner@example.com"}
        for provider in ("custom_imap", "google"):
            with self.subTest(provider=provider):
                handler = FakeHandler(initial_payload())
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=(user, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                    return_value=existing_connection_target(provider),
                ), patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save:
                    invoke_connect(handler)

                self.assertEqual(handler.status, 409)
                self.assertEqual(handler.response()["error"]["code"], "mailbox_id_conflict")
                preview.assert_not_called()
                save.assert_not_called()

    def test_reconnect_rejects_missing_and_non_custom_targets(self):
        user = {"email": "owner@example.com"}
        for target, expected_status, expected_code in (
            (missing_connection_target(), 404, "reconnect_target_not_found"),
            (existing_connection_target("google"), 409, "invalid_reconnect_target"),
        ):
            with self.subTest(expected_code=expected_code):
                handler = FakeHandler(initial_payload(mode="reconnect"))
                with patch.object(
                    connect_route,
                    "resolve_authenticated_user",
                    return_value=(user, None),
                ), patch.object(
                    connect_route,
                    "resolve_owned_managed_inbox_record",
                    return_value=target,
                ), patch.object(
                    imap_connect_preview,
                    "build_connect_preview_response",
                ) as preview, patch.object(
                    connect_route,
                    "save_mailbox_secret",
                ) as save:
                    invoke_connect(handler)

                self.assertEqual(handler.status, expected_status)
                self.assertEqual(handler.response()["error"]["code"], expected_code)
                preview.assert_not_called()
                save.assert_not_called()

    def _run_config_failure(self, mode, snapshot, rollback_error=None):
        handler = FakeHandler(initial_payload(mode=mode))
        user = {"email": "owner@example.com"}
        target = (
            missing_connection_target()
            if mode == "initial"
            else existing_connection_target()
        )
        with patch.object(
            connect_route,
            "resolve_authenticated_user",
            return_value=(user, None),
        ), patch.object(
            connect_route,
            "resolve_owned_managed_inbox_record",
            return_value=target,
        ), patch.object(
            imap_connect_preview,
            "build_connect_preview_response",
            return_value=(200, {"ok": True, "messages": []}),
        ), patch.object(
            connect_route,
            "snapshot_encrypted_mailbox_secret",
            return_value=snapshot,
        ), patch.object(
            connect_route,
            "save_mailbox_secret",
            return_value=({"imapPassword": "new-secret"}, None),
        ), patch.object(
            connect_route,
            "upsert_owned_custom_imap_mailbox",
            return_value={"status": "unavailable", "error": {"code": "offline"}},
        ), patch.object(
            connect_route,
            "restore_encrypted_mailbox_secret_snapshot",
            return_value=rollback_error,
        ) as restore:
            invoke_connect(handler)
        return handler, restore

    def test_config_failure_restores_previous_reconnect_secret(self):
        snapshot = {
            "status": "present",
            "record": {"ciphertext": "exact-prior-v2-record"},
            "error": None,
        }
        handler, restore = self._run_config_failure("reconnect", snapshot)

        self.assertEqual(handler.status, 503)
        self.assertFalse(handler.response()["ok"])
        restore.assert_called_once_with("owner@example.com", "demo", snapshot)

    def test_config_failure_removes_new_initial_secret(self):
        snapshot = {"status": "missing", "record": None, "error": None}
        handler, restore = self._run_config_failure("initial", snapshot)

        self.assertEqual(handler.status, 503)
        self.assertFalse(handler.response()["ok"])
        restore.assert_called_once_with("owner@example.com", "demo", snapshot)

    def test_rollback_failure_fails_closed(self):
        snapshot = {"status": "missing", "record": None, "error": None}
        handler, _ = self._run_config_failure(
            "initial",
            snapshot,
            {"code": "mailbox_secret_store_unavailable", "message": "offline"},
        )

        self.assertEqual(handler.status, 503)
        self.assertEqual(
            handler.response()["error"]["code"],
            "mailbox_connection_rollback_failed",
        )


class ExistingMailboxOperationTests(unittest.TestCase):
    def test_action_and_attachment_reject_browser_connection_fields(self):
        for route, payload in (
            (
                action_route,
                {"mailboxId": "demo", "folder": "INBOX", "uid": "1", "action": "mark_read", "host": "evil"},
            ),
            (
                attachment_route,
                {"mailboxId": "demo", "folder": "INBOX", "uid": "1", "attachmentId": "part-2", "password": "evil"},
            ),
        ):
            with self.subTest(route=route.__name__):
                handler = FakeHandler(payload)
                with patch.object(route, "resolve_authenticated_imap_mailbox") as resolver:
                    route.handler.do_POST(handler)
                self.assertEqual(handler.status, 400)
                self.assertEqual(handler.response()["error"]["code"], "forbidden_connection_fields")
                resolver.assert_not_called()

    def test_imap_action_uses_owned_credentials_and_uid_store(self):
        mailbox = Mock()
        mailbox.select.return_value = ("OK", [])
        mailbox.response.return_value = ("UIDVALIDITY", [b"456"])
        mailbox.uid.return_value = ("OK", [])
        handler = FakeHandler(
            {
                "mailboxId": "demo",
                "folder": "INBOX",
                "uid": "123",
                "uidValidity": "456",
                "action": "mark_read",
            }
        )
        with patch.object(action_route, "resolve_authenticated_imap_mailbox", return_value=resolved_mailbox()), patch.object(
            action_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "custom_imap"}},
        ), patch.object(
            action_route,
            "connect_mailbox_with_settings",
            return_value=mailbox,
        ) as connect:
            action_route.handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        connect.assert_called_once_with(
            host="imap.example.com",
            port=993,
            username="imap-user",
            password="imap-secret",
            ssl_enabled=True,
        )
        mailbox.uid.assert_called_once_with("store", "123", "+FLAGS.SILENT", "(\\Seen)")

    def test_gmail_action_dispatch_remains_on_existing_branch(self):
        handler = FakeHandler(
            {
                "mailboxId": "gmail-1",
                "messageId": "message-1",
                "action": "star",
            }
        )
        with patch.object(action_route, "_perform_gmail_action") as gmail_action, patch.object(
            action_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "google"}},
        ), patch.object(
            action_route,
            "resolve_gmail_context",
            return_value={"status": "ok", "context": {"access_token": "mock", "refresh_attempted": False}},
        ), patch.object(
            action_route,
            "resolve_authenticated_imap_mailbox",
        ) as imap_resolver:
            action_route.handler.do_POST(handler)
        gmail_action.assert_called_once_with(handler, ANY, "star", ANY)
        imap_resolver.assert_not_called()

    def test_custom_smtp_uses_server_derived_from_transport_and_password(self):
        handler = FakeHandler(
            {
                "mailboxId": "demo",
                "to": "recipient@example.com",
                "subject": "Subject",
                "bodyHtml": "<p>Body</p>",
                "bodyText": "Body",
                "attachments": [],
            }
        )
        smtp_instance = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp_instance)
        smtp_context.__exit__ = Mock(return_value=False)
        with patch.object(send_route, "resolve_authenticated_imap_mailbox", return_value=resolved_mailbox()), patch.object(
            send_route,
            "resolve_owned_mailbox",
            return_value={"status": "ok", "inbox": {"provider": "custom_imap"}},
        ), patch.object(
            send_route.smtplib,
            "SMTP",
            return_value=smtp_context,
        ) as smtp_constructor:
            send_route.handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        smtp_constructor.assert_called_once_with("smtp.example.com", 587, timeout=30)
        smtp_instance.login.assert_called_once_with("smtp-user", "smtp-secret")
        sent_message = smtp_instance.send_message.call_args.args[0]
        self.assertEqual(sent_message["From"], "demo@example.com")

    def test_custom_smtp_rejects_from_and_password_overrides(self):
        for forbidden in ({"from": "attacker@example.com"}, {"password": "evil"}):
            handler = FakeHandler(
                {
                    "mailboxId": "demo",
                    "to": "recipient@example.com",
                    "subject": "Subject",
                    "bodyHtml": "",
                    "bodyText": "Body",
                    **forbidden,
                }
            )
            with patch.object(send_route, "resolve_authenticated_imap_mailbox") as resolver:
                send_route.handler.do_POST(handler)
            self.assertEqual(handler.status, 400)
            resolver.assert_not_called()


class ChangedScopeGuardTests(unittest.TestCase):
    def test_gmail_thread_hydration_sources_are_unchanged_from_head(self):
        result = subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "frontend/api/inboxes/gmail_thread_parser.py",
                "frontend/src/lib/inboxConnectionApi.test.ts",
            ],
            cwd=FRONTEND_DIR.parent,
            check=False,
        )
        self.assertEqual(result.returncode, 0)


class CredentialsRouteTests(unittest.TestCase):
    def test_secret_writes_are_disabled(self):
        handler = FakeHandler({"mailboxId": "demo", "imapPassword": "secret"})
        credentials_route.handler.do_POST(handler)
        self.assertEqual(handler.status, 405)
        self.assertEqual(handler.response()["error"]["code"], "method_not_allowed")

    def test_status_is_authenticated_owned_and_never_returns_secret(self):
        handler = FakeHandler()
        handler.path = "/api/inboxes/credentials?mailboxIds=demo"
        with patch.object(
            credentials_route,
            "resolve_authenticated_user",
            return_value=(
                {
                    "email": "owner@example.com",
                    "name": "Owner",
                    "userType": "member",
                    "authSource": "auth0",
                },
                None,
            ),
        ), patch.object(
            credentials_route,
            "resolve_owned_managed_inbox",
            return_value={"status": "ok", "inbox": {"id": "demo"}, "error": None},
        ), patch.object(
            credentials_route,
            "read_mailbox_secret",
            return_value={
                "status": "present",
                "record": {"imapPassword": "secret", "smtpPassword": "other"},
                "error": None,
            },
        ):
            credentials_route.handler.do_GET(handler)
        self.assertEqual(handler.status, 200)
        response = handler.response()
        self.assertEqual(
            response["credentials"]["demo"],
            {"imapPasswordSet": True, "smtpPasswordSet": True},
        )
        self.assertNotIn("secret", json.dumps(response))


if __name__ == "__main__":
    unittest.main()
