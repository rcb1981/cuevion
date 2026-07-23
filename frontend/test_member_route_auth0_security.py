import importlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


FRONTEND_DIR = Path(__file__).resolve().parent
API_DIR = FRONTEND_DIR / "api"
INBOX_DIR = API_DIR / "inboxes"
for directory in (FRONTEND_DIR, API_DIR, INBOX_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from api.auth import runtime as auth_runtime


def load_route(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


support = importlib.import_module("api.contact.support")
organizer = load_route(
    API_DIR / "organizer" / "soundcloud-resolve.py",
    "organizer_soundcloud_member_auth_test",
)
credentials = load_route(
    INBOX_DIR / "credentials.py",
    "mailbox_credentials_member_auth_test",
)
connect_imap = load_route(
    INBOX_DIR / "connect-imap.py",
    "mailbox_connect_imap_member_auth_test",
)
oauth_callback = load_route(
    INBOX_DIR / "oauth-callback.py",
    "mailbox_oauth_callback_member_auth_test",
)


class HeaderMap(dict):
    def raw_items(self):
        return iter(list(self.items()))


class FakeHandler:
    def __init__(self, payload=None, *, headers=None, path="/"):
        body = json.dumps(payload or {}).encode("utf-8")
        self.headers = HeaderMap(
            {"content-length": str(len(body)), **(headers or {})}
        )
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.path = path
        self.status = None
        self.response_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        return None

    def _send_json(self, status, payload):
        connect_imap.handler._send_json(self, status, payload)

    def response_payload(self):
        return json.loads(self.wfile.getvalue())


def member(email="owner@example.com"):
    return auth_runtime.AuthenticatedMemberContext(
        user_id="user-1",
        email=email,
        name="Canonical Owner",
        workspace_id="workspace-1",
        membership_role="owner",
    )


def authenticated(email="owner@example.com"):
    return auth_runtime.AuthenticatedMemberResolution(
        auth_runtime.MemberResolutionOutcome.AUTHENTICATED,
        member(email),
    )


def unauthenticated(*, set_cookies=()):
    return auth_runtime.AuthenticatedMemberResolution(
        auth_runtime.MemberResolutionOutcome.UNAUTHENTICATED,
        None,
        set_cookies,
    )


def unavailable():
    return auth_runtime.AuthenticatedMemberResolution(
        auth_runtime.MemberResolutionOutcome.UNAVAILABLE,
        None,
    )


class SupportMemberAuthTests(unittest.TestCase):
    def test_unauthenticated_unavailable_and_malformed_headers_stop_all_io(self):
        for label, headers, resolution, expected_status in (
            ("unauthenticated", HeaderMap(), unauthenticated(), 401),
            ("authority_unavailable", HeaderMap(), unavailable(), 503),
            ("malformed_resolution", HeaderMap(), object(), 503),
            ("malformed_boundary", {}, None, 401),
        ):
            request = FakeHandler(
                {"subject": "Subject", "message": "Message"},
                headers=headers,
            )
            if label == "malformed_boundary":
                request.headers = headers
            body_reader = Mock()
            config_reader = Mock()
            provider = Mock()
            resolver_patch = (
                patch.object(
                    support.runtime,
                    "resolve_authenticated_member",
                    return_value=resolution,
                )
                if resolution is not None
                else patch.object(
                    support.runtime,
                    "resolve_authenticated_member",
                    side_effect=AssertionError("malformed headers reached auth runtime"),
                )
            )
            with self.subTest(label=label), resolver_patch, patch.object(
                support, "_read_json_body", body_reader
            ), patch.object(
                support, "_resolve_smtp_config", config_reader
            ), patch.object(
                support, "_send_support_email", provider
            ):
                support.handler.do_POST(request)
            self.assertEqual(request.status, expected_status)
            body_reader.assert_not_called()
            config_reader.assert_not_called()
            provider.assert_not_called()

    def test_authenticated_support_uses_only_canonical_member_identity(self):
        request = FakeHandler(
            {
                "subject": "Need help",
                "message": "Please help",
                "submittedBy": "Attacker <attacker@example.com>",
                "workspaceName": "Attacker workspace",
            }
        )
        smtp_config = {
            "to_email": "support@example.com",
            "from_email": "noreply@example.com",
            "host": "smtp.example.com",
            "port": 465,
            "username": "smtp-user",
            "password": "smtp-password",
            "secure": "ssl",
        }
        with patch.object(
            support.runtime,
            "resolve_authenticated_member",
            return_value=authenticated(),
        ), patch.object(
            support,
            "_resolve_smtp_config",
            return_value=(smtp_config, None),
        ), patch.object(support, "_send_support_email") as provider:
            support.handler.do_POST(request)

        self.assertEqual(request.status, 200)
        provider.assert_called_once()
        body = provider.call_args.args[1].get_content()
        self.assertIn("Canonical Owner <owner@example.com>", body)
        self.assertIn("Workspace: workspace-1", body)
        self.assertNotIn("attacker@example.com", body)
        self.assertNotIn("Attacker workspace", body)


class OrganizerMemberAuthTests(unittest.TestCase):
    def test_auth_failures_stop_before_soundcloud_provider(self):
        for label, headers, resolution, expected_status in (
            ("unauthenticated", HeaderMap(), unauthenticated(), 401),
            ("authority_unavailable", HeaderMap(), unavailable(), 503),
            ("malformed_resolution", HeaderMap(), object(), 503),
            ("malformed_boundary", {}, None, 401),
        ):
            request = FakeHandler({"url": "https://soundcloud.com/artist/track"})
            if label == "malformed_boundary":
                request.headers = headers
            resolver_patch = (
                patch.object(
                    organizer.runtime,
                    "resolve_authenticated_member",
                    return_value=resolution,
                )
                if resolution is not None
                else patch.object(
                    organizer.runtime,
                    "resolve_authenticated_member",
                    side_effect=AssertionError("malformed headers reached auth runtime"),
                )
            )
            with self.subTest(label=label), resolver_patch, patch.object(
                organizer, "_read_json_body"
            ) as body_reader, patch.object(
                organizer, "_resolve_soundcloud_preview"
            ) as provider:
                organizer.handler.do_POST(request)
            self.assertEqual(request.status, expected_status)
            body_reader.assert_not_called()
            provider.assert_not_called()

    def test_authenticated_member_can_resolve_preview(self):
        request = FakeHandler({"url": "https://soundcloud.com/artist/track"})
        preview = {
            "canonicalUrl": "https://soundcloud.com/artist/track",
            "height": 166,
            "iframeSrc": "https://w.soundcloud.com/player/?url=track",
            "title": "Track",
        }
        with patch.object(
            organizer.runtime,
            "resolve_authenticated_member",
            return_value=authenticated(),
        ), patch.object(
            organizer,
            "_resolve_soundcloud_preview",
            return_value=(preview, None),
        ) as provider:
            organizer.handler.do_POST(request)
        self.assertEqual(request.status, 200)
        self.assertTrue(request.response_payload()["ok"])
        provider.assert_called_once_with("https://soundcloud.com/artist/track")


class MailboxMemberAuthTests(unittest.TestCase):
    def test_historical_beta_cookie_cannot_read_or_write_mailbox_credentials(self):
        historical_cookie = "cuevion_beta_session=historical-member-cookie"
        read_request = FakeHandler(
            headers={"cookie": historical_cookie},
            path="/api/inboxes/credentials?mailboxIds=imap-1",
        )
        with patch.object(
            credentials, "resolve_owned_managed_inbox"
        ) as config_store, patch.object(
            credentials, "read_mailbox_secret"
        ) as secret_store:
            credentials.handler.do_GET(read_request)
        self.assertEqual(read_request.status, 401)
        config_store.assert_not_called()
        secret_store.assert_not_called()

        write_request = FakeHandler(
            {"mode": "initial", "mailboxId": "imap-1", "connection": {}},
            headers={"cookie": historical_cookie},
        )
        with patch.object(
            connect_imap, "save_mailbox_secret"
        ) as secret_write, patch.object(
            connect_imap, "upsert_owned_custom_imap_mailbox"
        ) as config_write, patch.object(
            connect_imap, "resolve_authenticated_imap_mailbox"
        ) as provider:
            connect_imap.handler.do_POST(write_request)
        self.assertEqual(write_request.status, 401)
        secret_write.assert_not_called()
        config_write.assert_not_called()
        provider.assert_not_called()

    def test_credentials_auth_failure_stops_before_config_and_secret_storage(self):
        for label, auth_error, expected_status in (
            ("unauthenticated", {"code": "missing_session"}, 401),
            ("authority_unavailable", {"code": "session_auth_unavailable"}, 503),
        ):
            request = FakeHandler(path="/api/inboxes/credentials?mailboxIds=imap-1")
            with self.subTest(label=label), patch.object(
                credentials,
                "resolve_authenticated_user",
                return_value=(None, auth_error),
            ), patch.object(
                credentials, "resolve_owned_managed_inbox"
            ) as config_store, patch.object(
                credentials, "read_mailbox_secret"
            ) as secret_store:
                credentials.handler.do_GET(request)
            self.assertEqual(request.status, expected_status)
            config_store.assert_not_called()
            secret_store.assert_not_called()

    def test_credentials_secret_lookup_uses_canonical_account_email(self):
        request = FakeHandler(path="/api/inboxes/credentials?mailboxIds=imap-1")
        canonical_user = {
            "email": "canonical@example.com",
            "name": "Canonical Owner",
            "userType": "member",
            "authSource": "auth0",
        }
        with patch.object(
            credentials,
            "resolve_authenticated_user",
            return_value=(canonical_user, None),
        ), patch.object(
            credentials,
            "resolve_owned_managed_inbox",
            return_value={"status": "ok", "inbox": {"id": "imap-1"}, "error": None},
        ), patch.object(
            credentials,
            "read_mailbox_secret",
            return_value={"status": "missing", "record": None, "error": None},
        ) as secret_store:
            credentials.handler.do_GET(request)
        self.assertEqual(request.status, 200)
        secret_store.assert_called_once_with("canonical@example.com", "imap-1")

    def test_oauth_callback_auth_outage_and_malformed_boundary_stop_all_io(self):
        for label, headers, resolution in (
            ("authority_unavailable", HeaderMap(), unavailable()),
            ("malformed_resolution", HeaderMap(), object()),
            ("malformed_boundary", {}, None),
        ):
            callback = Mock()
            callback.path = "/api/inboxes/oauth-callback?code=code&state=state"
            callback.headers = headers
            callback._send_callback_page = Mock()
            resolver_patch = (
                patch.object(
                    oauth_callback.runtime,
                    "resolve_authenticated_member",
                    return_value=resolution,
                )
                if resolution is not None
                else patch.object(
                    oauth_callback.runtime,
                    "resolve_authenticated_member",
                    side_effect=AssertionError("malformed headers reached auth runtime"),
                )
            )
            with self.subTest(label=label), resolver_patch, patch.object(
                oauth_callback, "_verify_signed_state_with_secrets"
            ) as state_reader, patch.object(
                oauth_callback, "_exchange_google_code"
            ) as provider, patch.object(
                oauth_callback, "persist_google_token_record"
            ) as token_store, patch.object(
                oauth_callback, "_upsert_gmail_managed_inbox_in_user_config"
            ) as config_store:
                oauth_callback.handler.do_GET(callback)
            state_reader.assert_not_called()
            provider.assert_not_called()
            token_store.assert_not_called()
            config_store.assert_not_called()
            payload = callback._send_callback_page.call_args.args[0]
            self.assertEqual(payload["status"], "error")
            self.assertNotIn("connected", payload)
            self.assertNotIn("mailboxId", payload)


if __name__ == "__main__":
    unittest.main()
