import importlib.util
import base64
import io
import json
import os
import sys
import unittest
from contextlib import ExitStack
from email.errors import MessageError
from email.message import EmailMessage
from email import message_from_bytes
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = API_DIR.parent
for directory in (CURRENT_DIR, API_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import authenticated_gmail
import imap_connect_preview
import oauth_token_store
import user_config_store
from api.auth import runtime as auth_runtime


def load_route(filename, name):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


connect_oauth = load_route("connect-oauth.py", "connect_oauth_ownership_test")
oauth_callback = load_route("oauth-callback.py", "oauth_callback_ownership_test")
config_route = load_route("../user/config.py", "user_config_google_ownership_test")
fetch_gmail = importlib.import_module("api.inboxes.fetch-gmail")
fetch_trash = importlib.import_module("api.inboxes.fetch-trash")
message_action = load_route("message-action.py", "message_action_ownership_test")
send_gmail = load_route("send-gmail.py", "send_gmail_ownership_test")
download_attachment = load_route("download-attachment.py", "download_attachment_ownership_test")
fetch_gmail_thread = load_route("fetch-gmail-thread.py", "fetch_gmail_thread_ownership_test")


class BoundaryResponse:
    def __init__(self, payload, *, headers=None):
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        self.headers = headers or {}
        self.read_amounts = []

    def read(self, amount=None):
        self.read_amounts.append(amount)
        return self.payload if amount is None else self.payload[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class OversizedStreamingResponse(BoundaryResponse):
    class OversizedChunk:
        def __init__(self, size):
            self.size = size

        def __len__(self):
            return self.size

    def __init__(self):
        super().__init__(b"")

    def read(self, amount=None):
        self.read_amounts.append(amount)
        return self.OversizedChunk((amount or 0) + 1)


class FailedReadResponse(BoundaryResponse):
    def read(self, amount=None):
        self.read_amounts.append(amount)
        raise URLError("raw response read failure")


class IncompleteReadResponse(BoundaryResponse):
    def read(self, amount=None):
        self.read_amounts.append(amount)
        raise IncompleteRead(b"partial", 10)


def inbox(**overrides):
    return {
        "id": "gmail-1",
        "email": "verified@gmail.com",
        "provider": "google",
        "connected": True,
        "connectionStatus": "connected",
        **overrides,
    }


def token(**overrides):
    return {
        "provider": "google",
        "email": "verified@gmail.com",
        "owner_email": "owner@example.com",
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "expires_at": None,
        "_storage_durable": True,
        **overrides,
    }


def durable_google_token(**overrides):
    return {
        "provider": "google",
        "email": "verified@gmail.com",
        "owner_email": "owner@example.com",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token",
        "token_type": "Bearer",
        "scope": "legacy-scope",
        "expires_at": "2025-01-01T01:00:00+00:00",
        "expires_in": 3600,
        "updated_at": "2025-01-01T00:00:00+00:00",
        "created_at": "2025-01-01T00:00:00+00:00",
        **overrides,
    }


class HeaderMap(dict):
    def raw_items(self):
        return iter(list(self.items()))


def authenticated_member(email="owner@example.com"):
    return auth_runtime.AuthenticatedMemberContext(
        user_id="user-1",
        email=email.strip().lower(),
        name="Owner",
        workspace_id="workspace-1",
        membership_role="owner",
    )


def member_session_cookie(email="owner@example.com"):
    return f"__Host-cuevion_session=test-member:{email.strip().lower()}"


def resolve_test_user(headers):
    raw_cookie = str(headers.get("cookie", ""))
    marker = "__Host-cuevion_session=test-member:"
    if not raw_cookie.startswith(marker):
        return None, {
            "code": "missing_session" if not raw_cookie else "invalid_session",
            "message": "An authenticated session is required.",
        }
    email = raw_cookie[len(marker):].strip().lower()
    if not email or "@" not in email:
        return None, {
            "code": "invalid_session",
            "message": "The authenticated session is invalid.",
        }
    return {
        "email": email,
        "name": "Owner",
        "userType": "member",
    }, None


def resolve_test_member_authority(headers):
    user, error = resolve_test_user(headers)
    if user is None:
        return None, error
    return authenticated_member(user["email"]), None


class FakeHandler:
    def __init__(self, payload=None, raw_body=None, headers=None):
        body = raw_body if raw_body is not None else json.dumps(payload or {}).encode()
        self.headers = HeaderMap(
            {"content-length": str(len(body)), **(headers or {})}
        )
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def _send_json(self, status, payload, *, write_body=True):
        connect_oauth.handler._send_json(self, status, payload, write_body=write_body)

    def payload(self):
        return json.loads(self.wfile.getvalue())


class RealHandlerOwnershipMatrixTests(unittest.TestCase):
    environment = {
        "KV_REST_API_URL": "https://kv.example",
        "KV_REST_API_TOKEN": "kv-secret",
        "GOOGLE_CLIENT_ID": "client-id",
        "GOOGLE_CLIENT_SECRET": "client-secret",
    }

    def _session_cookie(self, email="owner@example.com"):
        return member_session_cookie(email)

    def _route_cases(self):
        raw_message = base64.urlsafe_b64encode(
            b"From: sender@example.com\r\nTo: owner@example.com\r\nSubject: Empty\r\n\r\nBody"
        ).rstrip(b"=").decode()
        return [
            (fetch_gmail, {"mailboxId": "gmail-1"}, {"messages": []}, 200),
            (fetch_trash, {"mailboxId": "gmail-1"}, {"messages": []}, 200),
            (
                message_action,
                {"mailboxId": "gmail-1", "messageId": "message-1", "action": "star"},
                {},
                200,
            ),
            (
                send_gmail,
                {
                    "mailboxId": "gmail-1",
                    "to": "recipient@example.com",
                    "subject": "Subject",
                    "bodyText": "Body",
                },
                {"id": "sent-msg", "threadId": "sent-thread"},
                200,
            ),
            (
                download_attachment,
                {
                    "mailboxId": "gmail-1",
                    "messageId": "message-1",
                    "attachmentId": "missing-attachment",
                },
                {"raw": raw_message},
                404,
            ),
            (
                fetch_gmail_thread,
                {"mailboxId": "gmail-1", "providerThreadId": "thread-1"},
                {"id": "thread-1", "messages": []},
                200,
            ),
        ]

    def _refreshing_token_transport(self, initial_token, *, fail_persistence=False):
        state = {"record": initial_token, "exchange_calls": 0, "writes": 0}

        def transport(request, timeout):
            if request.full_url == oauth_token_store.GOOGLE_TOKEN_ENDPOINT:
                state["exchange_calls"] += 1
                return BoundaryResponse(
                    json.dumps({"access_token": "refreshed-access", "expires_in": 3600})
                )
            if request.get_method() == "POST":
                state["writes"] += 1
                if fail_persistence:
                    raise URLError("raw persistence outage")
                state["record"] = json.loads(request.data)
                return BoundaryResponse('{"result":"OK"}')
            return BoundaryResponse(
                json.dumps({"result": json.dumps(state["record"])})
            )

        return Mock(side_effect=transport), state

    def _invoke(
        self,
        route,
        payload,
        provider_payload,
        *,
        config=None,
        token_record=None,
        cookie=None,
        token_transport_override=None,
        provider_transport_override=None,
        config_transport_override=None,
    ):
        stored_config = config if config is not None else {
            "v": 1,
            "email": "owner@example.com",
            "managedInboxes": [inbox()],
        }
        stored_token = token_record if token_record is not None else token()
        request = FakeHandler(
            payload,
            headers={"cookie": cookie if cookie is not None else self._session_cookie()},
        )
        provider_transport = provider_transport_override or Mock(
            return_value=BoundaryResponse(json.dumps(provider_payload))
        )
        config_transport = config_transport_override or Mock(
            return_value=BoundaryResponse(
                json.dumps({"result": json.dumps(stored_config)})
            )
        )
        token_transport = token_transport_override or Mock(
            return_value=BoundaryResponse(
                json.dumps({"result": json.dumps(stored_token)})
            )
        )
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, self.environment, clear=False))
            stack.enter_context(
                patch.object(
                    sys.modules["user_config_store"],
                    "resolve_authenticated_user",
                    side_effect=resolve_test_user,
                )
            )
            stack.enter_context(patch.object(sys.modules["user_config_store"], "urlopen", config_transport))
            stack.enter_context(patch.object(oauth_token_store, "urlopen", token_transport))
            stack.enter_context(patch.object(route, "urlopen", provider_transport))
            route.handler.do_POST(request)
        return request, config_transport, token_transport, provider_transport

    def test_each_route_uses_real_session_config_owner_and_token_chain(self):
        for route, payload, provider_payload, expected_status in self._route_cases():
            with self.subTest(route=route.__name__):
                request, config_transport, token_transport, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                )
            self.assertEqual(request.status, expected_status)
            config_transport.assert_called_once()
            token_transport.assert_called_once()
            provider_transport.assert_called_once()

    def test_fetch_limit_actions_and_server_derived_from(self):
        request, _, _, provider_transport = self._invoke(
            fetch_gmail,
            {"mailboxId": "gmail-1", "limit": 999},
            {"messages": []},
        )
        self.assertEqual(request.status, 200)
        self.assertIn("maxResults=100", provider_transport.call_args.args[0].full_url)

        boolean_limit = FakeHandler({"mailboxId": "gmail-1", "limit": True})
        with patch.object(sys.modules["user_config_store"], "urlopen") as storage:
            fetch_gmail.handler.do_POST(boolean_limit)
        self.assertEqual(boolean_limit.status, 400)
        storage.assert_not_called()

        for action in ("mark_read", "mark_unread", "star", "unstar"):
            request, _, _, provider_transport = self._invoke(
                message_action,
                {"mailboxId": "gmail-1", "messageId": "message-1", "action": action},
                {},
            )
            self.assertEqual(request.status, 200)
            provider_transport.assert_called_once()

        unsupported = FakeHandler(
            {"mailboxId": "gmail-1", "messageId": "message-1", "action": "delete"}
        )
        with patch.object(sys.modules["user_config_store"], "urlopen") as storage:
            message_action.handler.do_POST(unsupported)
        self.assertEqual(unsupported.status, 400)
        storage.assert_not_called()

        request, _, _, provider_transport = self._invoke(
            send_gmail,
            {
                "mailboxId": "gmail-1",
                "to": "recipient@example.com",
                "subject": "Subject",
                "bodyText": "Body",
            },
            {"id": "sent-msg", "threadId": "sent-thread"},
        )
        provider_request = provider_transport.call_args.args[0]
        gmail_payload = json.loads(provider_request.data)
        self.assertEqual(set(gmail_payload), {"raw"})
        encoded_message = gmail_payload["raw"]
        decoded_message = base64.urlsafe_b64decode(
            encoded_message + "=" * (-len(encoded_message) % 4)
        )
        sent_message = message_from_bytes(decoded_message)
        self.assertEqual(
            sent_message.get("From"),
            "verified@gmail.com",
        )
        self.assertIsNone(sent_message.get("In-Reply-To"))
        self.assertIsNone(sent_message.get("References"))
        self.assertEqual(request.payload()["providerMessageId"], "sent-msg")
        self.assertEqual(request.payload()["providerThreadId"], "sent-thread")

    def test_each_route_rejects_missing_session_and_other_users_mailbox(self):
        invalid_cookies = (
            "",
            "__Host-cuevion_session=malformed",
            "cuevion_beta_session=historical-member-cookie",
        )
        for route, payload, provider_payload, _ in self._route_cases():
            for invalid_cookie in invalid_cookies:
                with self.subTest(route=route.__name__, cookie=invalid_cookie[:30]):
                    request, config_transport, token_transport, provider_transport = self._invoke(
                        route,
                        payload,
                        provider_payload,
                        cookie=invalid_cookie,
                    )
                self.assertEqual(request.status, 401)
                config_transport.assert_not_called()
                token_transport.assert_not_called()
                provider_transport.assert_not_called()

            with self.subTest(route=route.__name__, case="other_user"):
                request, _, token_transport, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    config={
                        "v": 1,
                        "email": "owner@example.com",
                        "managedInboxes": [inbox(id="other-mailbox")],
                    },
                )
            self.assertEqual(request.status, 404)
            token_transport.assert_not_called()
            provider_transport.assert_not_called()

    def test_each_route_rejects_identity_fields_before_storage_or_provider(self):
        forbidden_fields = {
            "email": "victim@gmail.com",
            "provider": "google",
            "authMode": "oauth",
            "from": "victim@gmail.com",
            "username": "victim",
            "password": "secret",
            "host": "evil.example",
            "port": 993,
            "smtpHost": "evil.example",
            "smtpPort": 465,
            "accessToken": "secret-token",
            "refreshToken": "secret-refresh",
            "ownerEmail": "other@example.com",
        }
        for route, payload, provider_payload, _ in self._route_cases():
            for field, value in forbidden_fields.items():
                with self.subTest(route=route.__name__, field=field):
                    request, config_transport, token_transport, provider_transport = self._invoke(
                        route,
                        {**payload, field: value},
                        provider_payload,
                    )
                self.assertEqual(request.status, 400)
                config_transport.assert_not_called()
                token_transport.assert_not_called()
                provider_transport.assert_not_called()

    def test_duplicate_disconnected_wrong_provider_and_legacy_tokens_fail_closed(self):
        base_payload = {"mailboxId": "gmail-1"}
        cases = [
            (
                {"v": 1, "email": "owner@example.com", "managedInboxes": [inbox(), inbox()]},
                token(),
                503,
                "user_config_store_unavailable",
            ),
            (
                {"v": 1, "email": "owner@example.com", "managedInboxes": [inbox(connected=False)]},
                token(),
                409,
                "gmail_connection_not_ready",
            ),
            (
                {"v": 1, "email": "owner@example.com", "managedInboxes": [inbox(provider="custom_imap")]},
                token(),
                400,
                "unsupported_provider",
            ),
            (
                {"v": 1, "email": "owner@example.com", "managedInboxes": [inbox()]},
                token(owner_email=None),
                401,
                "reconnect_required",
            ),
        ]
        for config, token_record, status, code in cases:
            with self.subTest(code=code):
                request, _, _, provider_transport = self._invoke(
                    fetch_gmail,
                    base_payload,
                    {"messages": []},
                    config=config,
                    token_record=token_record,
                )
            self.assertEqual(request.status, status)
            self.assertEqual(request.payload()["error"]["code"], code)
            provider_transport.assert_not_called()

    def test_each_route_rejects_non_exact_connection_status_before_token_lookup(self):
        status_values = (None, "", "CONNECTED", " connected ", 1, True, ["connected"])
        for route, payload, provider_payload, _ in self._route_cases():
            for status_value in status_values:
                mailbox = inbox()
                if status_value is None:
                    mailbox.pop("connectionStatus", None)
                else:
                    mailbox["connectionStatus"] = status_value
                with self.subTest(route=route.__name__, status=status_value):
                    request, _, token_transport, provider_transport = self._invoke(
                        route,
                        payload,
                        provider_payload,
                        config={
                            "v": 1,
                            "email": "owner@example.com",
                            "managedInboxes": [mailbox],
                        },
                    )
                self.assertIn(request.status, {409, 503})
                self.assertIn(
                    request.payload()["error"]["code"],
                    {"gmail_connection_not_ready", "user_config_store_unavailable"},
                )
                token_transport.assert_not_called()
                provider_transport.assert_not_called()

    def test_each_route_rejects_owner_mismatch_nondurable_and_malformed_tokens(self):
        malformed_records = (
            token(owner_email="other-owner@example.com"),
            token(access_token=None),
            token(access_token=123),
            token(expires_at="not-a-timestamp"),
            token(owner_email=["owner@example.com"]),
            token(provider="microsoft"),
            token(email=["verified@gmail.com"]),
        )
        for route, payload, provider_payload, _ in self._route_cases():
            for token_record in malformed_records:
                with self.subTest(route=route.__name__, token_record=token_record):
                    request, _, token_transport, provider_transport = self._invoke(
                        route,
                        payload,
                        provider_payload,
                        token_record=token_record,
                    )
                self.assertEqual(request.status, 401)
                self.assertEqual(request.payload()["error"]["code"], "reconnect_required")
                self.assertNotIn("other-owner", json.dumps(request.payload()))
                self.assertNotIn("access-secret", json.dumps(request.payload()))
                self.assertEqual(token_transport.call_count, 1)
                provider_transport.assert_not_called()

    def test_each_route_rejects_runtime_only_token_metadata(self):
        runtime_store = {
            oauth_token_store._build_store_key("verified@gmail.com"): token()
        }
        for route, payload, provider_payload, _ in self._route_cases():
            with self.subTest(route=route.__name__), patch.object(
                oauth_token_store,
                "_resolve_durable_store_config",
                return_value=None,
            ), patch.object(
                oauth_token_store,
                "_read_runtime_store",
                return_value=runtime_store,
            ), patch.object(
                oauth_token_store,
                "_exchange_google_refresh_token",
            ) as refresh_exchange:
                request, _, _, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                )
            self.assertEqual(request.status, 401)
            self.assertEqual(request.payload()["error"]["code"], "reconnect_required")
            self.assertNotIn("access-secret", json.dumps(request.payload()))
            refresh_exchange.assert_not_called()
            provider_transport.assert_not_called()

    def test_each_route_maps_token_store_outage_to_sanitized_503(self):
        for route, payload, provider_payload, _ in self._route_cases():
            token_transport = Mock(side_effect=URLError("raw token store outage"))
            with self.subTest(route=route.__name__):
                request, _, _, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    token_transport_override=token_transport,
                )
            self.assertEqual(request.status, 503)
            self.assertEqual(
                request.payload()["error"]["code"],
                "gmail_token_store_unavailable",
            )
            self.assertNotIn("raw", json.dumps(request.payload()))
            self.assertNotEqual(request.payload()["error"]["code"], "reconnect_required")
            provider_transport.assert_not_called()

    def test_each_route_rejects_unexpected_durable_token_shape(self):
        for route, payload, provider_payload, _ in self._route_cases():
            with self.subTest(route=route.__name__):
                request, _, token_transport, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    token_record=["unexpected", "token", "shape"],
                )
            self.assertEqual(request.status, 503)
            self.assertEqual(
                request.payload()["error"]["code"],
                "gmail_token_store_unavailable",
            )
            self.assertNotIn("unexpected", json.dumps(request.payload()))
            self.assertEqual(token_transport.call_count, 1)
            provider_transport.assert_not_called()

    def test_each_route_maps_config_store_outage_to_sanitized_503(self):
        for route, payload, provider_payload, _ in self._route_cases():
            config_transport = Mock(side_effect=URLError("raw config store outage"))
            with self.subTest(route=route.__name__):
                request, _, token_transport, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    config_transport_override=config_transport,
                )
            self.assertEqual(request.status, 503)
            self.assertEqual(
                request.payload()["error"]["code"],
                "user_config_store_unavailable",
            )
            self.assertNotIn("raw", json.dumps(request.payload()))
            token_transport.assert_not_called()
            provider_transport.assert_not_called()

    def test_each_route_proactively_refreshes_before_one_provider_request(self):
        for route, payload, provider_payload, _ in self._route_cases():
            token_transport, state = self._refreshing_token_transport(
                token(expires_at="2000-01-01T00:00:00Z")
            )
            with self.subTest(route=route.__name__):
                request, _, _, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    token_transport_override=token_transport,
                )
            self.assertNotEqual(request.status, 401)
            self.assertEqual(state["exchange_calls"], 1)
            self.assertEqual(state["writes"], 1)
            self.assertEqual(state["record"]["owner_email"], "owner@example.com")
            provider_transport.assert_called_once()
            self.assertEqual(
                provider_transport.call_args.args[0].get_header("Authorization"),
                "Bearer refreshed-access",
            )

    def test_each_route_refresh_persistence_outage_is_sanitized_503(self):
        for route, payload, provider_payload, _ in self._route_cases():
            token_transport, state = self._refreshing_token_transport(
                token(expires_at="2000-01-01T00:00:00Z"),
                fail_persistence=True,
            )
            with self.subTest(route=route.__name__):
                request, _, _, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    token_transport_override=token_transport,
                )
            self.assertEqual(request.status, 503)
            self.assertEqual(
                request.payload()["error"]["code"],
                "gmail_token_store_unavailable",
            )
            self.assertEqual(state["exchange_calls"], 1)
            self.assertEqual(state["writes"], 1)
            self.assertNotIn("raw persistence", json.dumps(request.payload()))
            provider_transport.assert_not_called()

    def test_each_route_retries_one_401_once_then_terminates(self):
        environment = {
            **self.environment,
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
        }
        for route, payload, _, _ in self._route_cases():
            current_token = token(refresh_token="refresh-secret")
            config = {
                "v": 1,
                "email": "owner@example.com",
                "managedInboxes": [inbox()],
            }
            config_transport = Mock(
                return_value=BoundaryResponse(
                    json.dumps({"result": json.dumps(config)})
                )
            )
            refresh_exchange_calls = 0

            def token_transport(request, timeout):
                nonlocal current_token, refresh_exchange_calls
                if request.full_url == oauth_token_store.GOOGLE_TOKEN_ENDPOINT:
                    refresh_exchange_calls += 1
                    return BoundaryResponse(
                        json.dumps({"access_token": "refreshed-access", "expires_in": 3600})
                    )
                if request.get_method() == "POST":
                    current_token = json.loads(request.data)
                    return BoundaryResponse('{"result":"OK"}')
                return BoundaryResponse(
                    json.dumps({"result": json.dumps(current_token)})
                )

            provider_calls = 0

            def revoked_provider(request, timeout):
                nonlocal provider_calls
                provider_calls += 1
                raise HTTPError(request.full_url, 401, "raw revoked", {}, io.BytesIO(b"raw provider"))

            request = FakeHandler(
                payload,
                headers={"cookie": self._session_cookie()},
            )
            with self.subTest(route=route.__name__), ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, environment, clear=False))
                stack.enter_context(
                    patch.object(
                        sys.modules["user_config_store"],
                        "resolve_authenticated_user",
                        side_effect=resolve_test_user,
                    )
                )
                stack.enter_context(
                    patch.object(sys.modules["user_config_store"], "urlopen", config_transport)
                )
                stack.enter_context(patch.object(oauth_token_store, "urlopen", side_effect=token_transport))
                stack.enter_context(patch.object(route, "urlopen", side_effect=revoked_provider))
                route.handler.do_POST(request)
            self.assertEqual(request.status, 401)
            self.assertEqual(request.payload()["error"]["code"], "reconnect_required")
            self.assertEqual(provider_calls, 2)
            self.assertEqual(refresh_exchange_calls, 1)
            self.assertNotIn("raw", json.dumps(request.payload()))

    def test_each_route_retries_one_401_and_can_succeed(self):
        for route, payload, provider_payload, _ in self._route_cases():
            token_transport, state = self._refreshing_token_transport(token())
            provider_calls = 0

            def provider_transport(request, timeout):
                nonlocal provider_calls
                provider_calls += 1
                if provider_calls == 1:
                    raise HTTPError(
                        request.full_url,
                        401,
                        "raw revoked",
                        {},
                        io.BytesIO(b"raw provider token=secret"),
                    )
                return BoundaryResponse(json.dumps(provider_payload))

            provider_mock = Mock(side_effect=provider_transport)
            with self.subTest(route=route.__name__):
                request, _, _, _ = self._invoke(
                    route,
                    payload,
                    provider_payload,
                    token_transport_override=token_transport,
                    provider_transport_override=provider_mock,
                )
            self.assertNotEqual(request.status, 401)
            self.assertEqual(provider_calls, 2)
            self.assertEqual(state["exchange_calls"], 1)
            self.assertEqual(state["writes"], 1)
            self.assertNotIn("raw provider", json.dumps(request.payload()))
            self.assertNotIn("secret", json.dumps(request.payload()))

    def test_each_route_does_not_refresh_unrelated_provider_failures(self):
        failures = (
            (403, HTTPError("https://gmail.invalid", 403, "raw denied", {}, io.BytesIO(b"raw forbidden"))),
            (429, HTTPError("https://gmail.invalid", 429, "raw quota", {}, io.BytesIO(b"raw quota"))),
            (500, HTTPError("https://gmail.invalid", 500, "raw provider", {}, io.BytesIO(b"raw provider"))),
            (502, URLError("raw network failure")),
        )
        for route, payload, provider_payload, _ in self._route_cases():
            for failure_status, failure in failures:
                token_transport = Mock(
                    return_value=BoundaryResponse(
                        json.dumps({"result": json.dumps(token())})
                    )
                )
                provider_transport = Mock(side_effect=failure)
                with self.subTest(route=route.__name__, failure=failure_status):
                    request, _, _, _ = self._invoke(
                        route,
                        payload,
                        provider_payload,
                        token_transport_override=token_transport,
                        provider_transport_override=provider_transport,
                    )
                self.assertIn(request.status, {403, 502})
                self.assertEqual(token_transport.call_count, 1)
                provider_transport.assert_called_once()
                response_text = json.dumps(request.payload())
                self.assertNotIn("reconnect_required", response_text)
                self.assertNotIn("raw", response_text)
                self.assertNotIn("access-secret", response_text)

    def test_each_route_returns_internal_error_for_unexpected_token_logic_failure(self):
        for route, payload, provider_payload, _ in self._route_cases():
            with self.subTest(route=route.__name__), patch.object(
                authenticated_gmail,
                "load_google_token_record_with_metadata",
                side_effect=KeyError("raw internal owner@example.com access-secret"),
            ):
                request, _, _, provider_transport = self._invoke(
                    route,
                    payload,
                    provider_payload,
                )
            self.assertEqual(request.status, 500)
            self.assertEqual(request.payload()["error"]["code"], "internal_error")
            response_text = json.dumps(request.payload())
            self.assertNotIn("raw internal", response_text)
            self.assertNotIn("owner@example.com", response_text)
            self.assertNotIn("access-secret", response_text)
            provider_transport.assert_not_called()

    def test_fetch_skips_malformed_base64_and_keeps_valid_messages(self):
        valid_raw = base64.urlsafe_b64encode(
            b"From: sender@example.com\r\nTo: owner@example.com\r\nSubject: Valid\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps({"messages": [{"id": "bad"}, {"id": "good"}]})),
                BoundaryResponse(json.dumps({"id": "bad", "raw": "%%%not-base64%%%"})),
                BoundaryResponse(json.dumps({"id": "good", "raw": valid_raw, "labelIds": ["INBOX"]})),
            )
        )
        request, _, _, _ = self._invoke(
            fetch_gmail,
            {"mailboxId": "gmail-1"},
            {},
            provider_transport_override=provider_transport,
        )
        self.assertEqual(request.status, 200)
        self.assertEqual(len(request.payload()["messages"]), 1)
        self.assertEqual(request.payload()["messages"][0]["subject"], "Valid")
        self.assertEqual(provider_transport.call_count, 3)

    def test_fetch_does_not_publish_list_id_when_detail_omits_label_ids(self):
        omitted_labels_raw = base64.urlsafe_b64encode(
            b"From: stale@example.com\r\nTo: owner@example.com\r\nSubject: Omitted labels\r\n\r\nBody"
        ).rstrip(b"=").decode()
        valid_raw = base64.urlsafe_b64encode(
            b"From: current@example.com\r\nTo: owner@example.com\r\nSubject: Current Inbox\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(
                    json.dumps(
                        {
                            "messages": [
                                {"id": "missing-labels"},
                                {"id": "current-inbox"},
                            ]
                        }
                    )
                ),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "missing-labels",
                            "raw": omitted_labels_raw,
                        }
                    )
                ),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "current-inbox",
                            "raw": valid_raw,
                            "labelIds": ["INBOX"],
                        }
                    )
                ),
            )
        )

        request, _, _, _ = self._invoke(
            fetch_gmail,
            {"mailboxId": "gmail-1"},
            {},
            provider_transport_override=provider_transport,
        )

        self.assertEqual(request.status, 200)
        payload = request.payload()
        self.assertEqual(
            [message["providerMessageId"] for message in payload["messages"]],
            ["current-inbox"],
        )
        self.assertEqual(payload["inboxUidSet"], ["current-inbox"])
        self.assertNotIn("missing-labels", json.dumps(payload))
        self.assertEqual(provider_transport.call_count, 3)

    def test_fetch_does_not_publish_list_id_when_detail_lacks_inbox_label(self):
        stale_membership_raw = base64.urlsafe_b64encode(
            b"From: stale@example.com\r\nTo: owner@example.com\r\nSubject: Stale membership\r\n\r\nBody"
        ).rstrip(b"=").decode()
        valid_raw = base64.urlsafe_b64encode(
            b"From: current@example.com\r\nTo: owner@example.com\r\nSubject: Current Inbox\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(
                    json.dumps(
                        {
                            "messages": [
                                {"id": "stale-membership"},
                                {"id": "current-inbox"},
                            ]
                        }
                    )
                ),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "stale-membership",
                            "raw": stale_membership_raw,
                            "labelIds": ["UNREAD", "STARRED"],
                        }
                    )
                ),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "current-inbox",
                            "raw": valid_raw,
                            "labelIds": ["INBOX", "STARRED"],
                        }
                    )
                ),
            )
        )

        request, _, _, _ = self._invoke(
            fetch_gmail,
            {"mailboxId": "gmail-1"},
            {},
            provider_transport_override=provider_transport,
        )

        self.assertEqual(request.status, 200)
        payload = request.payload()
        self.assertEqual(
            [message["providerMessageId"] for message in payload["messages"]],
            ["current-inbox"],
        )
        self.assertEqual(payload["inboxUidSet"], ["current-inbox"])
        self.assertNotIn("stale-membership", json.dumps(payload))
        self.assertEqual(provider_transport.call_count, 3)

    def test_fetch_skips_only_documented_message_parse_errors(self):
        malformed_raw = base64.urlsafe_b64encode(b"malformed provider message").rstrip(b"=").decode()
        valid_raw = base64.urlsafe_b64encode(
            b"From: sender@example.com\r\nTo: owner@example.com\r\nSubject: Valid\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps({"messages": [{"id": "bad"}, {"id": "good"}]})),
                BoundaryResponse(json.dumps({"id": "bad", "raw": malformed_raw, "labelIds": []})),
                BoundaryResponse(json.dumps({"id": "good", "raw": valid_raw, "labelIds": ["INBOX"]})),
            )
        )
        real_parser = fetch_gmail.message_from_bytes

        def parse_message(raw):
            if raw == b"malformed provider message":
                raise MessageError("documented malformed email")
            return real_parser(raw)

        with patch.object(fetch_gmail, "message_from_bytes", side_effect=parse_message):
            request, _, _, _ = self._invoke(
                fetch_gmail,
                {"mailboxId": "gmail-1"},
                {},
                provider_transport_override=provider_transport,
            )
        self.assertEqual(request.status, 200)
        self.assertEqual(len(request.payload()["messages"]), 1)
        self.assertEqual(request.payload()["messages"][0]["subject"], "Valid")

    def test_fetch_valueerror_from_message_parser_aborts_without_partial_success(self):
        first_raw = base64.urlsafe_b64encode(
            b"From: first@example.com\r\nSubject: First\r\n\r\nBody"
        ).rstrip(b"=").decode()
        second_raw = base64.urlsafe_b64encode(
            b"From: second@example.com\r\nSubject: Second\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps({"messages": [{"id": "first"}, {"id": "second"}]})),
                BoundaryResponse(json.dumps({"id": "first", "raw": first_raw, "labelIds": ["INBOX"]})),
                BoundaryResponse(json.dumps({"id": "second", "raw": second_raw, "labelIds": ["INBOX"]})),
            )
        )
        real_parser = fetch_gmail.message_from_bytes
        parse_calls = 0

        def parse_message(raw):
            nonlocal parse_calls
            parse_calls += 1
            if parse_calls == 2:
                raise ValueError("raw parser regression access-secret")
            return real_parser(raw)

        with patch.object(fetch_gmail, "message_from_bytes", side_effect=parse_message):
            request, _, _, _ = self._invoke(
                fetch_gmail,
                {"mailboxId": "gmail-1"},
                {},
                provider_transport_override=provider_transport,
            )
        self.assertEqual(request.status, 500)
        self.assertEqual(request.payload()["error"]["code"], "internal_error")
        self.assertNotIn("messages", request.payload())
        self.assertNotIn("raw parser", json.dumps(request.payload()))
        self.assertNotIn("access-secret", json.dumps(request.payload()))

    def test_fetch_preview_programming_errors_are_fatal_and_sanitized(self):
        first_raw = base64.urlsafe_b64encode(
            b"From: first@example.com\r\nTo: owner@example.com\r\nSubject: First\r\n\r\nBody"
        ).rstrip(b"=").decode()
        second_raw = base64.urlsafe_b64encode(
            b"From: second@example.com\r\nTo: owner@example.com\r\nSubject: Second\r\n\r\nBody"
        ).rstrip(b"=").decode()
        real_preview = imap_connect_preview.to_message_preview
        failures = (
            ValueError("raw preview ValueError access-secret"),
            TypeError("raw preview TypeError access-secret"),
            KeyError("raw preview KeyError access-secret"),
            AttributeError("raw preview AttributeError access-secret"),
        )
        for failure in failures:
            provider_transport = Mock(
                side_effect=(
                    BoundaryResponse(json.dumps({"messages": [{"id": "first"}, {"id": "second"}]})),
                    BoundaryResponse(json.dumps({"id": "first", "raw": first_raw, "labelIds": ["INBOX"]})),
                    BoundaryResponse(json.dumps({"id": "second", "raw": second_raw, "labelIds": ["INBOX"]})),
                )
            )
            preview_calls = 0

            def build_preview(*args, **kwargs):
                nonlocal preview_calls
                preview_calls += 1
                if preview_calls == 2:
                    raise failure
                return real_preview(*args, **kwargs)

            with self.subTest(exception=type(failure).__name__), patch.object(
                imap_connect_preview,
                "to_message_preview",
                side_effect=build_preview,
            ):
                request, _, _, _ = self._invoke(
                    fetch_gmail,
                    {"mailboxId": "gmail-1"},
                    {},
                    provider_transport_override=provider_transport,
                )
            self.assertEqual(request.status, 500)
            self.assertEqual(request.payload()["error"]["code"], "internal_error")
            self.assertNotIn("messages", request.payload())
            self.assertNotIn("raw preview", json.dumps(request.payload()))
            self.assertNotIn("access-secret", json.dumps(request.payload()))
            self.assertIn(("Cache-Control", "no-store"), request.response_headers)

    def test_storage_exception_returns_sanitized_json_503(self):
        request = FakeHandler(
            {"mailboxId": "gmail-1"},
            headers={"cookie": self._session_cookie()},
        )
        token_transport = Mock()
        provider_transport = Mock()
        with patch.dict(os.environ, self.environment, clear=False), patch.object(
            sys.modules["user_config_store"],
            "resolve_authenticated_user",
            side_effect=resolve_test_user,
        ), patch.object(
            sys.modules["user_config_store"],
            "urlopen",
            side_effect=RuntimeError("raw storage URL https://secret.example token=secret"),
        ), patch.object(oauth_token_store, "urlopen", token_transport), patch.object(
            fetch_gmail, "urlopen", provider_transport
        ):
            fetch_gmail.handler.do_POST(request)
        self.assertEqual(request.status, 503)
        self.assertEqual(
            request.payload()["error"]["code"],
            "user_config_store_unavailable",
        )
        self.assertNotIn("secret.example", json.dumps(request.payload()))
        token_transport.assert_not_called()
        provider_transport.assert_not_called()

    def test_each_route_validation_methods_options_and_cache_contract(self):
        for route, _, _, _ in self._route_cases():
            request_limit = (
                authenticated_gmail.MAX_SEND_REQUEST_BODY_BYTES
                if route is send_gmail
                else authenticated_gmail.MAX_SMALL_REQUEST_BODY_BYTES
            )
            invalid_requests = [
                FakeHandler(raw_body=b"{"),
                FakeHandler(raw_body=b"\xff"),
                FakeHandler(raw_body=b"[]"),
                FakeHandler(raw_body=b"{}", headers={"content-length": "invalid"}),
                FakeHandler(raw_body=b"{}", headers={"content-length": "-1"}),
                FakeHandler(
                    raw_body=b"{}",
                    headers={"content-length": str(request_limit + 1)},
                ),
            ]
            for request in invalid_requests:
                provider_transport = Mock()
                with self.subTest(route=route.__name__, headers=request.headers), patch.object(
                    route,
                    "urlopen",
                    provider_transport,
                ):
                    route.handler.do_POST(request)
                self.assertIn(request.status, {400, 413})
                self.assertIn(("Cache-Control", "no-store"), request.response_headers)
                provider_transport.assert_not_called()

            method_request = FakeHandler({})
            route.handler.do_GET(method_request)
            self.assertEqual(method_request.status, 405)
            self.assertEqual(method_request.payload()["error"]["code"], "method_not_allowed")
            self.assertIn(("Cache-Control", "no-store"), method_request.response_headers)

            options_request = FakeHandler({})
            route.handler.do_OPTIONS(options_request)
            self.assertEqual(options_request.status, 200)
            self.assertEqual(options_request.payload(), {"ok": True})
            self.assertIn(("Cache-Control", "no-store"), options_request.response_headers)


class CentralResolverTests(unittest.TestCase):
    def test_authentication_ownership_and_mailbox_validation(self):
        cases = [
            ({"status": "unauthorized"}, 401, "unauthorized"),
            ({"status": "not_found"}, 404, "gmail_connection_not_found"),
            ({"status": "unavailable"}, 503, "user_config_store_unavailable"),
        ]
        for owned, status, code in cases:
            with self.subTest(code=code), patch.object(
                authenticated_gmail, "resolve_owned_managed_inbox_record", return_value=owned
            ):
                result = authenticated_gmail.resolve_owned_mailbox({}, "gmail-1")
                self.assertEqual(result["status_code"], status)
                self.assertEqual(result["error"]["error"]["code"], code)

        for mailbox_id in (None, "", " x", "x\n", "x" * 257):
            with self.subTest(mailbox_id=mailbox_id), patch.object(
                authenticated_gmail, "resolve_owned_managed_inbox_record"
            ) as resolver:
                result = authenticated_gmail.resolve_owned_mailbox({}, mailbox_id)
                self.assertEqual(result["status_code"], 400)
                resolver.assert_not_called()

    def test_provider_readiness_owner_and_token_binding(self):
        user = {"email": "Owner@Example.com"}
        for mailbox, status, code in (
            (inbox(provider="custom_imap"), 400, "unsupported_provider"),
            (inbox(connected=False), 409, "gmail_connection_not_ready"),
            (inbox(connectionStatus=None), 409, "gmail_connection_not_ready"),
        ):
            with self.subTest(code=code):
                result = authenticated_gmail.resolve_gmail_context({"user": user, "inbox": mailbox})
                self.assertEqual(result["status_code"], status)
                self.assertEqual(result["error"]["error"]["code"], code)

        for record in (
            None,
            token(owner_email=None),
            token(owner_email="other@example.com"),
            token(_storage_durable=False),
        ):
            with self.subTest(record=record), patch.object(
                authenticated_gmail,
                "load_google_token_record_with_metadata",
                return_value=(record, None),
            ), patch.object(authenticated_gmail, "refresh_google_token_record") as refresh:
                result = authenticated_gmail.resolve_gmail_context({"user": user, "inbox": inbox()})
                self.assertEqual(result["status_code"], 401)
                self.assertEqual(result["error"]["error"]["code"], "reconnect_required")
                refresh.assert_not_called()

    def test_owned_token_success_storage_failure_and_expired_refresh(self):
        owned = {"user": {"email": "owner@example.com"}, "inbox": inbox()}
        with patch.object(
            authenticated_gmail, "load_google_token_record_with_metadata", return_value=(token(), None)
        ):
            result = authenticated_gmail.resolve_gmail_context(owned)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["context"]["mailbox_email"], "verified@gmail.com")

        with patch.object(
            authenticated_gmail,
            "load_google_token_record_with_metadata",
            return_value=(None, {"code": "gmail_token_store_unavailable", "message": "raw storage detail"}),
        ):
            result = authenticated_gmail.resolve_gmail_context(owned)
        self.assertEqual(result["status_code"], 503)
        self.assertNotIn("raw storage detail", json.dumps(result))

        expired = token(expires_at="2000-01-01T00:00:00Z")
        refreshed = token(access_token="new-secret")
        with patch.object(
            authenticated_gmail, "load_google_token_record_with_metadata", return_value=(expired, None)
        ), patch.object(
            authenticated_gmail, "refresh_google_token_record", return_value=(refreshed, None)
        ) as refresh:
            result = authenticated_gmail.resolve_gmail_context(owned)
        self.assertEqual(result["context"]["access_token"], "new-secret")
        self.assertTrue(result["context"]["refresh_attempted"])
        refresh.assert_called_once_with("verified@gmail.com", owner_email="owner@example.com")

    def test_bounded_parsing_unknown_fields_and_identifiers(self):
        cases = [
            (b"{", None),
            (b"\xff", None),
            (b"[]", None),
            (b"{}", {"content-length": "bad"}),
            (b"{}", {"content-length": "-1"}),
            (b"{}", {"content-length": str(authenticated_gmail.MAX_SMALL_REQUEST_BODY_BYTES + 1)}),
        ]
        for raw, headers in cases:
            with self.subTest(raw=raw, headers=headers):
                _, error = authenticated_gmail.read_json_body(FakeHandler(raw_body=raw, headers=headers))
                self.assertIsNotNone(error)
        for field in authenticated_gmail.FORBIDDEN_IDENTITY_FIELDS:
            self.assertIsNotNone(authenticated_gmail.reject_unknown_fields({field: "secret"}, {"mailboxId"}))


class TokenOwnerBindingTests(unittest.TestCase):
    def test_record_and_refresh_preserve_normalized_owner(self):
        record = oauth_token_store.build_google_token_record(
            email="verified@gmail.com",
            owner_email=" Owner@Example.com ",
            token_payload={"access_token": "access", "refresh_token": "refresh"},
        )
        self.assertEqual(record["owner_email"], "owner@example.com")

        with patch.object(
            oauth_token_store,
            "_load_existing_google_record",
            return_value=("key", {"backend": "kv"}, record, None),
        ), patch.object(
            oauth_token_store,
            "_exchange_google_refresh_token",
            return_value=({"access_token": "new"}, None),
        ) as exchange, patch.object(
            oauth_token_store,
            "_persist_google_record",
            side_effect=lambda **kwargs: ({**kwargs["record"], "_storage_durable": True}, None),
        ):
            refreshed, error = oauth_token_store.refresh_google_token_record(
                "verified@gmail.com", owner_email="OWNER@example.com"
            )
        self.assertIsNone(error)
        self.assertEqual(refreshed["owner_email"], "owner@example.com")
        exchange.assert_called_once_with(refresh_token="refresh")

    def test_mismatch_and_legacy_owner_never_refresh(self):
        for owner in (None, "other@example.com"):
            existing = token(owner_email=owner)
            with self.subTest(owner=owner), patch.object(
                oauth_token_store,
                "_load_existing_google_record",
                return_value=("key", {"backend": "kv"}, existing, None),
            ), patch.object(oauth_token_store, "_exchange_google_refresh_token") as exchange:
                record, error = oauth_token_store.refresh_google_token_record(
                    "verified@gmail.com", owner_email="owner@example.com"
                )
                self.assertIsNone(record)
                self.assertEqual(error["code"], "gmail_reconnect_required")
                exchange.assert_not_called()

    def test_durable_store_boundary_failures_are_unavailable_and_sanitized(self):
        config = {"rest_url": "https://kv.secret.example", "rest_token": "secret-token"}
        bad_responses = [
            BoundaryResponse(b""),
            BoundaryResponse(b"{"),
            BoundaryResponse(b"\xff"),
            BoundaryResponse(b"x" * (oauth_token_store.MAX_OAUTH_RESPONSE_BYTES + 1)),
            BoundaryResponse(json.dumps([])),
            BoundaryResponse(json.dumps({"unexpected": "shape"})),
            BoundaryResponse(json.dumps({"result": ""})),
        ]
        for response in bad_responses:
            with self.subTest(size=len(response.payload)), patch.object(
                oauth_token_store,
                "urlopen",
                return_value=response,
            ):
                record, error = oauth_token_store._read_durable_record(config, "token-key")
            self.assertIsNone(record)
            self.assertEqual(error["code"], "gmail_token_store_unavailable")
            serialized = json.dumps(error)
            self.assertNotIn("kv.secret.example", serialized)
            self.assertNotIn("secret-token", serialized)

        for failure in (
            TimeoutError("raw timeout"),
            URLError("raw url"),
            OSError("raw os"),
            RuntimeError("raw unexpected"),
        ):
            with self.subTest(failure=type(failure).__name__), patch.object(
                oauth_token_store,
                "urlopen",
                side_effect=failure,
            ):
                record, error = oauth_token_store._read_durable_record(config, "token-key")
            self.assertIsNone(record)
            self.assertEqual(error["code"], "gmail_token_store_unavailable")
            self.assertNotIn("raw", json.dumps(error))

    def test_central_resolver_preserves_typed_storage_failures_and_exposes_programming_errors(self):
        owned = {"user": {"email": "owner@example.com"}, "inbox": inbox()}
        with patch.object(
            authenticated_gmail,
            "load_google_token_record_with_metadata",
            return_value=(None, {"code": "gmail_token_store_unavailable"}),
        ):
            result = authenticated_gmail.resolve_gmail_context(owned)
        self.assertEqual(result["status_code"], 503)
        self.assertEqual(result["error"]["error"]["code"], "gmail_token_store_unavailable")

        with patch.object(
            authenticated_gmail,
            "refresh_google_token_record",
            return_value=(None, {"code": "gmail_token_store_unavailable"}),
        ):
            result = authenticated_gmail.refresh_gmail_context(
                {
                    "mailbox_id": "gmail-1",
                    "mailbox_email": "verified@gmail.com",
                    "owner_email": "owner@example.com",
                    "access_token": "old",
                    "refresh_attempted": False,
                }
            )
        self.assertEqual(result["status_code"], 503)
        self.assertEqual(result["error"]["error"]["code"], "gmail_token_store_unavailable")

        with patch.object(
            authenticated_gmail,
            "refresh_google_token_record",
            return_value=(None, {"code": "token_persistence_failed"}),
        ):
            persistence_result = authenticated_gmail.refresh_gmail_context(
                {
                    "mailbox_id": "gmail-1",
                    "mailbox_email": "verified@gmail.com",
                    "owner_email": "owner@example.com",
                    "access_token": "old",
                    "refresh_attempted": False,
                }
            )
        self.assertEqual(persistence_result["status_code"], 503)
        self.assertEqual(
            persistence_result["error"]["error"]["code"],
            "gmail_token_store_unavailable",
        )

        for helper_name, callable_under_test in (
            (
                "load_google_token_record_with_metadata",
                lambda: authenticated_gmail.resolve_gmail_context(owned),
            ),
            (
                "refresh_google_token_record",
                lambda: authenticated_gmail.refresh_gmail_context(
                    {
                        "mailbox_id": "gmail-1",
                        "mailbox_email": "verified@gmail.com",
                        "owner_email": "owner@example.com",
                        "access_token": "old",
                        "refresh_attempted": False,
                    }
                ),
            ),
        ):
            with self.subTest(helper=helper_name), patch.object(
                authenticated_gmail,
                helper_name,
                side_effect=KeyError("raw programming detail"),
            ), self.assertRaises(KeyError):
                callable_under_test()


class FocusPreferencesTests(unittest.TestCase):
    def test_exact_schema_and_bounds(self):
        valid = {
            "demos": "high",
            "promo": "medium",
            "finance": "low",
            "legal": "medium",
            "business": "low",
            "updates": "medium",
            "distribution": "high",
            "royalties": "low",
            "promoReminders": "medium",
            "paymentReminders": "low",
        }
        validated, error = fetch_gmail._validate_focus_preferences(valid)
        self.assertIsNone(error)
        self.assertEqual(validated, valid)

        invalid_values = [
            None,
            [],
            "medium",
            1,
            True,
            {"unknown": "low"},
            {"promo": 1},
            {"promo": "urgent"},
            {"promo": {"nested": "low"}},
            {**valid, "extra": "low"},
            {"x" * 33: "low"},
            {"promo": "x" * 17},
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                validated, error = fetch_gmail._validate_focus_preferences(value)
            self.assertIsNone(validated)
            self.assertEqual(error["error"]["code"], "invalid_focus_preferences")

    def test_invalid_focus_preferences_stop_before_owner_token_and_gmail(self):
        request = FakeHandler(
            {"mailboxId": "gmail-1", "focusPreferences": {"unknown": "low"}}
        )
        with patch.object(sys.modules["user_config_store"], "urlopen") as config_transport, patch.object(
            oauth_token_store, "urlopen"
        ) as token_transport, patch.object(fetch_gmail, "urlopen") as gmail_transport:
            fetch_gmail.handler.do_POST(request)
        self.assertEqual(request.status, 400)
        config_transport.assert_not_called()
        token_transport.assert_not_called()
        gmail_transport.assert_not_called()


class GmailReplyThreadContinuityTests(unittest.TestCase):
    def setUp(self):
        self.matrix = RealHandlerOwnershipMatrixTests()

    def base_payload(self, **overrides):
        return {
            "mailboxId": "gmail-1",
            "to": "recipient@example.com",
            "subject": "Re: Subject",
            "bodyText": "Reply body",
            "replyContext": {"sourceProviderMessageId": "source-msg"},
            **overrides,
        }

    def source_payload(self, **overrides):
        return {
            "id": "source-msg",
            "threadId": "thread-123",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<source@example.com>"},
                    {"name": "References", "value": "<root@example.com>"},
                    {"name": "Subject", "value": "Subject"},
                ]
            },
            **overrides,
        }

    def invoke(self, provider_transport, *, payload=None):
        return self.matrix._invoke(
            send_gmail,
            payload if payload is not None else self.base_payload(),
            {},
            provider_transport_override=provider_transport,
        )

    def decoded_send(self, provider_transport):
        provider_request = provider_transport.call_args_list[1].args[0]
        self.assertEqual(provider_request.get_method(), "POST")
        self.assertEqual(
            urlparse(provider_request.full_url).path,
            "/gmail/v1/users/me/messages/send",
        )
        gmail_payload = json.loads(provider_request.data)
        encoded_message = gmail_payload["raw"]
        decoded_message = base64.urlsafe_b64decode(
            encoded_message + "=" * (-len(encoded_message) % 4)
        )
        return gmail_payload, message_from_bytes(decoded_message)

    def test_reply_fetches_source_and_sends_provider_thread_with_rfc_headers(self):
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps(self.source_payload())),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "sent-msg",
                            "threadId": "thread-123",
                            "labelIds": ["SENT"],
                        }
                    )
                ),
            )
        )

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(request.status, 200)
        response = request.payload()
        self.assertTrue(response["ok"])
        self.assertEqual(response["providerMessageId"], "sent-msg")
        self.assertEqual(response["providerThreadId"], "thread-123")
        self.assertEqual(response.get("labelIds"), ["SENT"])
        self.assertNotIn("raw", response)
        self.assertEqual(provider_transport.call_count, 2)

        source_request = provider_transport.call_args_list[0].args[0]
        self.assertEqual(source_request.get_method(), "GET")
        parsed_source_url = urlparse(source_request.full_url)
        self.assertEqual(
            parsed_source_url.path,
            "/gmail/v1/users/me/messages/source-msg",
        )
        source_query = parse_qs(parsed_source_url.query)
        self.assertEqual(source_query.get("format"), ["metadata"])
        self.assertCountEqual(
            source_query.get("metadataHeaders", []),
            ["Message-ID", "References", "In-Reply-To", "Subject"],
        )

        gmail_payload, message = self.decoded_send(provider_transport)
        self.assertEqual(set(gmail_payload), {"raw", "threadId"})
        self.assertEqual(gmail_payload["threadId"], "thread-123")
        self.assertEqual(message.get("In-Reply-To"), "<source@example.com>")
        self.assertEqual(
            " ".join(str(message.get("References")).split()),
            "<root@example.com> <source@example.com>",
        )

    def test_reply_accepts_encoded_subject_and_uses_source_only_reference_fallback(self):
        source_payload = self.source_payload(
            payload={
                "headers": [
                    {
                        "name": "Message-ID",
                        "value": "<source@[127.0.0.1]>",
                    },
                    {
                        "name": "Subject",
                        "value": "=?utf-8?q?R=C3=A9sum=C3=A9?=",
                    },
                ]
            }
        )
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps(source_payload)),
                BoundaryResponse(
                    json.dumps({"id": "sent-msg", "threadId": "thread-123"})
                ),
            )
        )

        request, _, _, _ = self.invoke(
            provider_transport,
            payload=self.base_payload(subject="RE: Résumé"),
        )

        self.assertEqual(request.status, 200)
        gmail_payload, message = self.decoded_send(provider_transport)
        self.assertEqual(gmail_payload["threadId"], "thread-123")
        self.assertEqual(message.get("In-Reply-To"), "<source@[127.0.0.1]>")
        self.assertEqual(message.get("References"), "<source@[127.0.0.1]>")

    def test_source_404_fails_closed_before_gmail_send(self):
        provider_transport = Mock(
            side_effect=HTTPError(
                "https://gmail.invalid/messages/source-msg",
                404,
                "raw missing source",
                {},
                io.BytesIO(b"raw provider response"),
            )
        )

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(provider_transport.call_count, 1)
        self.assertEqual(
            provider_transport.call_args.args[0].get_method(),
            "GET",
        )
        self.assertIn(request.status, {400, 404})
        response = request.payload()
        self.assertFalse(response["ok"])
        self.assertIn("reply", response["error"]["code"])
        self.assertNotIn("raw missing source", json.dumps(response))

    def test_truncated_source_response_fails_closed_before_gmail_send(self):
        provider_transport = Mock(return_value=IncompleteReadResponse(b""))

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(provider_transport.call_count, 1)
        self.assertEqual(provider_transport.call_args.args[0].get_method(), "GET")
        self.assertEqual(request.status, 502)
        self.assertFalse(request.payload()["ok"])
        self.assertEqual(
            request.payload()["error"]["code"],
            "gmail_reply_source_invalid",
        )

    def test_invalid_source_authority_fails_closed_before_gmail_send(self):
        invalid_sources = {
            "provider_id_mismatch": self.source_payload(id="different-msg"),
            "missing_thread_id": self.source_payload(threadId=None),
            "oversized_thread_id": self.source_payload(threadId="t" * 257),
            "missing_rfc_message_id": self.source_payload(
                payload={
                    "headers": [
                        {"name": "References", "value": "<root@example.com>"},
                        {"name": "Subject", "value": "Subject"},
                    ]
                }
            ),
            "bare_rfc_message_id": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "source@example.com"},
                        {"name": "Subject", "value": "Subject"},
                    ]
                }
            ),
            "duplicate_rfc_message_id": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {"name": "Message-ID", "value": "<other@example.com>"},
                        {"name": "Subject", "value": "Subject"},
                    ]
                }
            ),
            "duplicate_subject": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {"name": "Subject", "value": "Subject"},
                        {"name": "Subject", "value": "Another subject"},
                    ]
                }
            ),
            "unrelated_subject": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {"name": "Subject", "value": "Unrelated campaign"},
                    ]
                }
            ),
            "oversized_source_subject": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {
                            "name": "Subject",
                            "value": "s" * (send_gmail.MAX_SUBJECT_CHARACTERS + 1),
                        },
                    ]
                }
            ),
            "malformed_encoded_source_subject": self.source_payload(
                payload={
                    "headers": [
                        {"name": "Message-ID", "value": "<source@example.com>"},
                        {"name": "Subject", "value": "=?utf-8?b?a?="},
                    ]
                }
            ),
        }

        for case, source_payload in invalid_sources.items():
            provider_transport = Mock(
                return_value=BoundaryResponse(json.dumps(source_payload))
            )
            with self.subTest(case=case):
                request, _, _, _ = self.invoke(provider_transport)
                self.assertEqual(provider_transport.call_count, 1)
                self.assertEqual(
                    provider_transport.call_args.args[0].get_method(),
                    "GET",
                )
                self.assertIn(request.status, {400, 422, 502})
                self.assertFalse(request.payload()["ok"])
                self.assertIn("reply", request.payload()["error"]["code"])

    def test_source_metadata_get_refreshes_once_before_single_send(self):
        token_transport, refresh_state = self.matrix._refreshing_token_transport(
            token()
        )
        provider_call_count = 0

        def provider_transport(request, timeout):
            nonlocal provider_call_count
            provider_call_count += 1
            if provider_call_count == 1:
                raise HTTPError(
                    request.full_url,
                    401,
                    "raw revoked source lookup",
                    {},
                    io.BytesIO(b"raw revoked provider response"),
                )
            if provider_call_count == 2:
                return BoundaryResponse(json.dumps(self.source_payload()))
            return BoundaryResponse(
                json.dumps(
                    {
                        "id": "sent-msg",
                        "threadId": "thread-123",
                        "labelIds": ["SENT"],
                    }
                )
            )

        gmail_transport = Mock(side_effect=provider_transport)
        request, _, _, _ = self.matrix._invoke(
            send_gmail,
            self.base_payload(),
            {},
            token_transport_override=token_transport,
            provider_transport_override=gmail_transport,
        )

        self.assertEqual(request.status, 200)
        self.assertEqual(
            [
                provider_call.args[0].get_method()
                for provider_call in gmail_transport.call_args_list
            ],
            ["GET", "GET", "POST"],
        )
        self.assertEqual(
            [
                provider_call.args[0].get_header("Authorization")
                for provider_call in gmail_transport.call_args_list
            ],
            [
                "Bearer access-secret",
                "Bearer refreshed-access",
                "Bearer refreshed-access",
            ],
        )
        self.assertEqual(refresh_state["exchange_calls"], 1)
        self.assertEqual(refresh_state["writes"], 1)
        self.assertEqual(request.payload()["providerMessageId"], "sent-msg")

    def test_source_metadata_get_stops_after_second_unauthorized_response(self):
        token_transport, refresh_state = self.matrix._refreshing_token_transport(
            token()
        )

        def revoked_provider(request, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "raw revoked source lookup",
                {},
                io.BytesIO(b"raw revoked provider response"),
            )

        gmail_transport = Mock(side_effect=revoked_provider)
        request, _, _, _ = self.matrix._invoke(
            send_gmail,
            self.base_payload(),
            {},
            token_transport_override=token_transport,
            provider_transport_override=gmail_transport,
        )

        self.assertEqual(
            [
                provider_call.args[0].get_method()
                for provider_call in gmail_transport.call_args_list
            ],
            ["GET", "GET"],
        )
        self.assertEqual(refresh_state["exchange_calls"], 1)
        self.assertEqual(refresh_state["writes"], 1)
        self.assertEqual(request.status, 401)
        self.assertEqual(request.payload()["error"]["code"], "reconnect_required")
        self.assertNotIn("raw revoked", json.dumps(request.payload()))

    def test_references_are_sanitized_deduplicated_and_bounded(self):
        historic_references = [
            f"<root-{index}@example.com>" for index in range(300)
        ]
        unsafe_references = " ".join(
            [
                historic_references[0],
                "not-a-message-id",
                "<broken>",
                *historic_references,
                historic_references[0],
                "<source@example.com>",
                "\r\nBcc:",
                "victim@example.com",
            ]
        )
        source_payload = self.source_payload(
            payload={
                "headers": [
                    {"name": "Message-ID", "value": "<source@example.com>"},
                    {"name": "References", "value": unsafe_references},
                    {"name": "Subject", "value": "Subject"},
                ]
            }
        )
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps(source_payload)),
                BoundaryResponse(
                    json.dumps(
                        {"id": "sent-msg", "threadId": "thread-123"}
                    )
                ),
            )
        )

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(request.status, 200)
        _, message = self.decoded_send(provider_transport)
        references = " ".join(str(message.get("References")).split()).split(" ")
        self.assertLessEqual(len(references), 50)
        self.assertLessEqual(len(" ".join(references)), 4096)
        self.assertEqual(len(references), len(set(references)))
        self.assertEqual(references[-1], "<source@example.com>")
        self.assertNotIn("not-a-message-id", references)
        self.assertNotIn("<broken>", references)
        self.assertNotIn("victim@example.com", references)

    def test_reply_context_rejects_spoofed_authority_and_malformed_shapes(self):
        invalid_payloads = {
            "top_level_spoof": {
                **self.base_payload(),
                "providerThreadId": "another-thread",
                "threadId": "gmail:gmail-1:another-thread",
                "rfcMessageId": "fake@example.com",
                "References": "<fake@example.com>",
                "In-Reply-To": "<fake@example.com>",
            },
            "nested_spoof": self.base_payload(
                replyContext={
                    "sourceProviderMessageId": "source-msg",
                    "providerThreadId": "another-thread",
                    "threadId": "gmail:gmail-1:another-thread",
                    "rfcMessageId": "fake@example.com",
                    "References": "<fake@example.com>",
                    "In-Reply-To": "<fake@example.com>",
                }
            ),
            "not_an_object": self.base_payload(replyContext="source-msg"),
            "empty_object": self.base_payload(replyContext={}),
            "blank_source": self.base_payload(
                replyContext={"sourceProviderMessageId": " "}
            ),
            "oversized_source": self.base_payload(
                replyContext={"sourceProviderMessageId": "x" * 257}
            ),
            "nested_source": self.base_payload(
                replyContext={"sourceProviderMessageId": {"id": "source-msg"}}
            ),
        }

        for case, payload in invalid_payloads.items():
            with self.subTest(case=case):
                request, config_transport, token_transport, provider_transport = (
                    self.invoke(Mock(), payload=payload)
                )
                self.assertEqual(request.status, 400)
                self.assertFalse(request.payload()["ok"])
                config_transport.assert_not_called()
                token_transport.assert_not_called()
                provider_transport.assert_not_called()

    def test_malformed_send_identity_is_sent_unconfirmed_without_resend(self):
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps(self.source_payload())),
                BoundaryResponse("{}"),
            )
        )

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(provider_transport.call_count, 2)
        self.assertEqual(
            [
                provider_call.args[0].get_method()
                for provider_call in provider_transport.call_args_list
            ],
            ["GET", "POST"],
        )
        self.assertEqual(request.status, 200)
        response = request.payload()
        self.assertTrue(response["ok"])
        self.assertIs(response.get("providerIdentityConfirmed"), False)
        self.assertIs(response.get("threadContinuityConfirmed"), False)
        self.assertNotIn("providerMessageId", response)
        self.assertNotIn("providerThreadId", response)
        self.assertEqual(
            response["warning"]["code"],
            "gmail_send_identity_unconfirmed",
        )

    def test_unusable_success_response_is_sent_unconfirmed_without_resend(self):
        response_factories = {
            "invalid_utf8": lambda: BoundaryResponse(b"\xff"),
            "invalid_json": lambda: BoundaryResponse("not-json"),
            "non_object_json": lambda: BoundaryResponse("[]"),
            "oversized": OversizedStreamingResponse,
            "read_failure_after_response": lambda: FailedReadResponse(b""),
            "truncated_response": lambda: IncompleteReadResponse(b""),
        }

        for case, response_factory in response_factories.items():
            provider_transport = Mock(return_value=response_factory())
            with self.subTest(case=case):
                request, _, _, _ = self.matrix._invoke(
                    send_gmail,
                    {
                        "mailboxId": "gmail-1",
                        "to": "recipient@example.com",
                        "subject": "Subject",
                        "bodyText": "Body",
                    },
                    {},
                    provider_transport_override=provider_transport,
                )
                self.assertEqual(provider_transport.call_count, 1)
                self.assertEqual(
                    provider_transport.call_args.args[0].get_method(),
                    "POST",
                )
                self.assertEqual(request.status, 200)
                response = request.payload()
                self.assertTrue(response["ok"])
                self.assertIs(response.get("providerIdentityConfirmed"), False)
                self.assertNotIn("providerMessageId", response)
                self.assertNotIn("providerThreadId", response)

    def test_returned_thread_mismatch_is_sent_unconfirmed_without_resend(self):
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps(self.source_payload())),
                BoundaryResponse(
                    json.dumps(
                        {
                            "id": "sent-msg",
                            "threadId": "different-thread",
                            "labelIds": ["SENT"],
                        }
                    )
                ),
            )
        )

        request, _, _, _ = self.invoke(provider_transport)

        self.assertEqual(provider_transport.call_count, 2)
        self.assertEqual(
            [
                provider_call.args[0].get_method()
                for provider_call in provider_transport.call_args_list
            ],
            ["GET", "POST"],
        )
        self.assertEqual(request.status, 200)
        response = request.payload()
        self.assertTrue(response["ok"])
        self.assertEqual(response["providerMessageId"], "sent-msg")
        self.assertEqual(response["providerThreadId"], "different-thread")
        self.assertIs(response.get("threadContinuityConfirmed"), False)


class SendLimitTests(unittest.TestCase):
    def setUp(self):
        self.matrix = RealHandlerOwnershipMatrixTests()

    def base_payload(self, **overrides):
        return {
            "mailboxId": "gmail-1",
            "to": "recipient@example.com",
            "subject": "Subject",
            "bodyText": "Body",
            **overrides,
        }

    def invoke(self, payload):
        return self.matrix._invoke(
            send_gmail,
            payload,
            {"id": "sent-msg", "threadId": "sent-thread"},
        )

    def assert_accepted(self, payload):
        request, config_transport, token_transport, provider_transport = self.invoke(payload)
        self.assertEqual(request.status, 200)
        self.assertEqual(
            request.payload(),
            {
                "ok": True,
                "providerMessageId": "sent-msg",
                "providerThreadId": "sent-thread",
            },
        )
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        config_transport.assert_called_once()
        self.assertEqual(token_transport.call_count, 1)
        self.assertNotEqual(
            token_transport.call_args.args[0].full_url,
            oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
        )
        provider_transport.assert_called_once()

    def assert_rejected(self, payload, *raw_markers):
        request, config_transport, token_transport, provider_transport = self.invoke(payload)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        config_transport.assert_called_once()
        self.assertEqual(token_transport.call_count, 1)
        self.assertNotEqual(
            token_transport.call_args.args[0].full_url,
            oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
        )
        provider_transport.assert_not_called()
        response_text = json.dumps(request.payload())
        self.assertNotIn("access-secret", response_text)
        for marker in raw_markers:
            self.assertNotIn(marker, response_text)

    def test_recipient_count_boundaries_through_authenticated_handler(self):
        recipients = [
            f"recipient-{index}@example.com"
            for index in range(send_gmail.MAX_RECIPIENTS + 1)
        ]
        self.assert_accepted(self.base_payload(to=",".join(recipients[:100])))
        self.assert_rejected(
            self.base_payload(to=",".join(recipients)),
            "recipient-100@example.com",
        )

    def test_subject_boundaries_and_header_injection_through_authenticated_handler(self):
        self.assert_accepted(
            self.base_payload(subject="s" * send_gmail.MAX_SUBJECT_CHARACTERS)
        )
        self.assert_rejected(
            self.base_payload(subject="s" * (send_gmail.MAX_SUBJECT_CHARACTERS + 1))
        )
        self.assert_rejected(
            self.base_payload(subject="Safe\r\nBcc: victim@example.com"),
            "victim@example.com",
        )

    def test_body_text_and_html_boundaries_through_authenticated_handler(self):
        self.assert_accepted(
            self.base_payload(bodyText="t" * send_gmail.MAX_BODY_CHARACTERS)
        )
        self.assert_rejected(
            self.base_payload(bodyText="t" * (send_gmail.MAX_BODY_CHARACTERS + 1))
        )
        self.assert_accepted(
            self.base_payload(bodyHtml="h" * send_gmail.MAX_BODY_CHARACTERS)
        )
        self.assert_rejected(
            self.base_payload(bodyHtml="h" * (send_gmail.MAX_BODY_CHARACTERS + 1))
        )

    def test_attachment_count_boundaries_through_authenticated_handler(self):
        attachments = [
            {
                "name": f"attachment-{index}.txt",
                "mimeType": "text/plain",
                "contentBase64": "eA==",
            }
            for index in range(send_gmail.MAX_ATTACHMENTS + 1)
        ]
        self.assert_accepted(self.base_payload(attachments=attachments[:10]))
        self.assert_rejected(
            self.base_payload(attachments=attachments),
            "attachment-10.txt",
        )

    def test_decoded_attachment_total_and_base64_boundaries_through_handler(self):
        exact_content = base64.b64encode(
            b"x" * send_gmail.MAX_TOTAL_ATTACHMENT_BYTES
        ).decode()
        self.assert_accepted(
            self.base_payload(
                attachments=[
                    {
                        "name": "exact.bin",
                        "mimeType": "application/octet-stream",
                        "contentBase64": exact_content,
                    }
                ]
            )
        )

        over_content = base64.b64encode(
            b"x" * (send_gmail.MAX_TOTAL_ATTACHMENT_BYTES + 1)
        ).decode()
        self.assert_rejected(
            self.base_payload(
                attachments=[
                    {
                        "name": "over.bin",
                        "mimeType": "application/octet-stream",
                        "contentBase64": over_content,
                    }
                ]
            )
        )
        self.assert_rejected(
            self.base_payload(
                attachments=[
                    {
                        "name": "bad.bin",
                        "mimeType": "application/octet-stream",
                        "contentBase64": "raw-invalid-base64%%",
                    }
                ]
            ),
            "raw-invalid-base64",
        )

    def test_maximum_documented_payload_combination_fits_and_sends_through_handler(self):
        self.assertEqual(authenticated_gmail.MAX_SEND_REQUEST_BODY_BYTES, 32 * 1024 * 1024)
        encoded_attachment = base64.b64encode(
            b"x" * send_gmail.MAX_TOTAL_ATTACHMENT_BYTES
        ).decode()
        payload = {
            "mailboxId": "gmail-1",
            "to": "recipient@example.com",
            "subject": "Subject",
            "bodyHtml": "h" * send_gmail.MAX_BODY_CHARACTERS,
            "bodyText": "t" * send_gmail.MAX_BODY_CHARACTERS,
            "attachments": [
                {
                    "name": "maximum.bin",
                    "mimeType": "application/octet-stream",
                    "contentBase64": encoded_attachment,
                }
            ],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.assertLess(len(raw), authenticated_gmail.MAX_SEND_REQUEST_BODY_BYTES)
        self.assertGreater(len(raw), 12 * 1024 * 1024)
        self.assert_accepted(payload)

    def test_request_above_32_mib_is_rejected_before_json_or_provider_processing(self):
        request = FakeHandler(
            raw_body=b"{}",
            headers={
                "content-length": str(
                    authenticated_gmail.MAX_SEND_REQUEST_BODY_BYTES + 1
                ),
                "cookie": self.matrix._session_cookie(),
            },
        )
        config_transport = Mock()
        token_transport = Mock()
        provider_transport = Mock()
        with patch.dict(
            os.environ,
            self.matrix.environment,
            clear=False,
        ), patch.object(
            sys.modules["user_config_store"],
            "resolve_authenticated_user",
            side_effect=resolve_test_user,
        ), patch.object(
            sys.modules["user_config_store"],
            "urlopen",
            config_transport,
        ), patch.object(
            oauth_token_store,
            "urlopen",
            token_transport,
        ), patch.object(
            send_gmail,
            "urlopen",
            provider_transport,
        ):
            send_gmail.handler.do_POST(request)
        self.assertEqual(request.status, 413)
        self.assertEqual(request.payload()["error"]["code"], "request_too_large")
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        config_transport.assert_not_called()
        token_transport.assert_not_called()
        provider_transport.assert_not_called()


class AttachmentDownloadLimitTests(unittest.TestCase):
    def setUp(self):
        self.matrix = RealHandlerOwnershipMatrixTests()

    def attachment_provider_payload(self, content=b"attachment-content"):
        message = EmailMessage()
        message["From"] = "sender@example.com"
        message["To"] = "verified@gmail.com"
        message["Subject"] = "Attachment"
        message.set_content("Body")
        message.add_attachment(
            content,
            maintype="application",
            subtype="octet-stream",
            filename="evidence.bin",
        )
        raw_message = message.as_bytes()
        attachments = imap_connect_preview.get_message_attachments(
            message_from_bytes(raw_message)
        )
        return (
            {
                "id": "message-1",
                "raw": base64.urlsafe_b64encode(raw_message).rstrip(b"=").decode(),
                "labelIds": [],
            },
            attachments[0]["id"],
            raw_message,
        )

    def invoke(self, provider_transport, *, attachment_id):
        return self.matrix._invoke(
            download_attachment,
            {
                "mailboxId": "gmail-1",
                "messageId": "message-1",
                "attachmentId": attachment_id,
            },
            {},
            provider_transport_override=provider_transport,
        )

    def assert_safe_oversize(self, request, token_transport, provider_transport):
        self.assertEqual(request.status, 502)
        self.assertEqual(
            request.payload()["error"]["code"],
            "gmail_response_too_large",
        )
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        self.assertNotIn("raw provider", json.dumps(request.payload()))
        self.assertEqual(token_transport.call_count, 1)
        self.assertNotEqual(
            token_transport.call_args.args[0].full_url,
            oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
        )
        provider_transport.assert_called_once()

    def test_raw_message_within_bound_downloads_valid_attachment_through_handler(self):
        provider_payload, attachment_id, raw_message = self.attachment_provider_payload()
        self.assertLess(len(raw_message), authenticated_gmail.MAX_GMAIL_RAW_MESSAGE_BYTES)
        provider_transport = Mock(
            return_value=BoundaryResponse(json.dumps(provider_payload))
        )
        request, config_transport, token_transport, _ = self.invoke(
            provider_transport,
            attachment_id=attachment_id,
        )
        self.assertEqual(request.status, 200)
        self.assertEqual(request.wfile.getvalue(), b"attachment-content")
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        config_transport.assert_called_once()
        self.assertEqual(token_transport.call_count, 1)
        provider_transport.assert_called_once()

    def test_raw_message_over_25_mib_is_rejected_without_reading_body(self):
        response = BoundaryResponse(
            b"raw provider body must not be read",
            headers={
                "Content-Length": str(
                    authenticated_gmail.MAX_GMAIL_RAW_MESSAGE_BYTES + 1
                )
            },
        )
        provider_transport = Mock(return_value=response)
        request, _, token_transport, _ = self.invoke(
            provider_transport,
            attachment_id="attachment-2",
        )
        self.assert_safe_oversize(request, token_transport, provider_transport)
        self.assertEqual(response.read_amounts, [])
        self.assertEqual(request.wfile.getvalue(), json.dumps(request.payload()).encode("utf-8"))

    def test_oversized_attachment_payload_is_rejected_before_allocation(self):
        minimum_encoded_attachment_size = (
            (authenticated_gmail.MAX_GMAIL_RAW_MESSAGE_BYTES + 1 + 2) // 3
        ) * 4
        response = BoundaryResponse(
            b"raw provider attachment must not be read",
            headers={"Content-Length": str(minimum_encoded_attachment_size)},
        )
        provider_transport = Mock(return_value=response)
        request, _, token_transport, _ = self.invoke(
            provider_transport,
            attachment_id="attachment-2",
        )
        self.assert_safe_oversize(request, token_transport, provider_transport)
        self.assertEqual(response.read_amounts, [])

    def test_streamed_excess_is_read_once_with_a_hard_bound(self):
        response = OversizedStreamingResponse()
        provider_transport = Mock(return_value=response)
        request, _, token_transport, _ = self.invoke(
            provider_transport,
            attachment_id="attachment-2",
        )
        self.assert_safe_oversize(request, token_transport, provider_transport)
        self.assertEqual(
            response.read_amounts,
            [authenticated_gmail.MAX_GMAIL_RAW_MESSAGE_BYTES + 1],
        )

    def test_missing_attachment_keeps_deterministic_safe_error(self):
        provider_payload, _, _ = self.attachment_provider_payload()
        provider_transport = Mock(
            return_value=BoundaryResponse(json.dumps(provider_payload))
        )
        request, _, token_transport, _ = self.invoke(
            provider_transport,
            attachment_id="missing-attachment",
        )
        self.assertEqual(request.status, 404)
        self.assertEqual(request.payload()["error"]["code"], "attachment_not_found")
        self.assertIn(("Cache-Control", "no-store"), request.response_headers)
        self.assertEqual(token_transport.call_count, 1)
        provider_transport.assert_called_once()

    def test_download_401_refreshes_and_retries_exactly_once(self):
        provider_payload, attachment_id, _ = self.attachment_provider_payload()
        token_transport, state = self.matrix._refreshing_token_transport(token())
        provider_transport = Mock(
            side_effect=(
                HTTPError(
                    "https://gmail.invalid",
                    401,
                    "raw revoked",
                    {},
                    io.BytesIO(b"raw provider token"),
                ),
                BoundaryResponse(json.dumps(provider_payload)),
            )
        )
        request, _, _, _ = self.matrix._invoke(
            download_attachment,
            {
                "mailboxId": "gmail-1",
                "messageId": "message-1",
                "attachmentId": attachment_id,
            },
            {},
            token_transport_override=token_transport,
            provider_transport_override=provider_transport,
        )
        self.assertEqual(request.status, 200)
        self.assertEqual(provider_transport.call_count, 2)
        self.assertEqual(state["exchange_calls"], 1)
        self.assertEqual(state["writes"], 1)
        self.assertNotIn(b"raw provider", request.wfile.getvalue())

    def test_download_403_429_and_500_never_refresh_or_retry(self):
        failures = (
            (403, "gmail_permission_denied"),
            (429, "gmail_rate_limited"),
            (500, "gmail_attachment_download_failed"),
        )
        for status, expected_code in failures:
            provider_transport = Mock(
                side_effect=HTTPError(
                    "https://gmail.invalid",
                    status,
                    "raw provider failure",
                    {},
                    io.BytesIO(b"raw provider body"),
                )
            )
            with self.subTest(status=status):
                request, _, token_transport, _ = self.invoke(
                    provider_transport,
                    attachment_id="attachment-2",
                )
            self.assertIn(request.status, {403, 502})
            self.assertEqual(request.payload()["error"]["code"], expected_code)
            self.assertEqual(token_transport.call_count, 1)
            self.assertNotEqual(
                token_transport.call_args.args[0].full_url,
                oauth_token_store.GOOGLE_TOKEN_ENDPOINT,
            )
            provider_transport.assert_called_once()
            self.assertNotIn("raw provider", json.dumps(request.payload()))


class OAuthAndConfigTests(unittest.TestCase):
    def _authenticated_headers(self, email="owner@example.com"):
        return HeaderMap({"cookie": member_session_cookie(email)})

    def _incomplete_onboarding_config(
        self,
        *,
        selected_inboxes=("main",),
        completed=False,
    ):
        return {
            "v": 1,
            "email": "owner@example.com",
            "onboardingSession": {
                "schemaVersion": 1,
                "completed": completed,
                "currentStep": 2,
                "choices": {"selectedInboxes": list(selected_inboxes)},
            },
            "managedInboxes": [],
        }

    def _google_callback(self, state):
        callback = Mock()
        callback.path = (
            f"/api/inboxes/oauth-callback?code=provider-code&state={state}"
        )
        callback.headers = HeaderMap()
        callback._send_callback_page = Mock()
        return callback

    def _production_oauth_environment(self, **overrides):
        return {
            "VERCEL_ENV": "production",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            **overrides,
        }

    def _run_logged_google_callback(
        self,
        *,
        state=None,
        member_result=None,
        environment=None,
        authorization_code="provider-code",
        provider_error=None,
        exchange_result=({"access_token": "provider-access-token"}, None),
        exchange_side_effect=None,
        identity_result=(
            {"email": "verified@gmail.com", "display_name": "Verified"},
            None,
        ),
        preflight_result=({"prepared": True}, None),
        persistence_result=({"_storage_durable": True}, None),
        registration_result=({"id": "gmail-verified"}, None),
        callback_time=None,
        logger_side_effect=None,
        response_side_effect=None,
        capture_exception=False,
    ):
        from urllib.parse import urlencode

        if state is None:
            state, verifier = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "state-secret",
                "main",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        else:
            verifier = None

        query = {"state": state}
        if authorization_code is not None:
            query["code"] = authorization_code
        if provider_error is not None:
            query["error"] = provider_error
        callback = Mock()
        callback.path = f"/api/inboxes/oauth-callback?{urlencode(query)}"
        callback.headers = HeaderMap()
        callback._send_callback_page = Mock(side_effect=response_side_effect)

        resolved_member = (
            (authenticated_member(), ())
            if member_result is None
            else member_result
        )
        logger = Mock()
        logger.warning.side_effect = logger_side_effect
        exchange = Mock(
            return_value=exchange_result,
            side_effect=exchange_side_effect,
        )
        identity = Mock(return_value=identity_result)
        preflight = Mock(return_value=preflight_result)
        token_store = Mock(return_value=persistence_result)
        config_store = Mock(return_value=registration_result)
        caught_exception = None

        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    oauth_callback.os.environ,
                    environment or self._production_oauth_environment(),
                    clear=True,
                )
            )
            stack.enter_context(
                patch.object(oauth_callback, "_GMAIL_CALLBACK_LOGGER", logger)
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "_resolve_authenticated_member_request",
                    return_value=resolved_member,
                )
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "_exchange_google_code",
                    new=exchange,
                )
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "_fetch_verified_google_identity",
                    new=identity,
                )
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "_prepare_gmail_managed_inbox_registration",
                    new=preflight,
                )
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "persist_google_token_record",
                    new=token_store,
                )
            )
            stack.enter_context(
                patch.object(
                    oauth_callback,
                    "_register_gmail_managed_inbox_in_user_config",
                    new=config_store,
                )
            )
            if callback_time is not None:
                stack.enter_context(
                    patch.object(
                        oauth_callback.time,
                        "time",
                        return_value=callback_time,
                    )
                )

            if capture_exception:
                try:
                    oauth_callback.handler.do_GET(callback)
                except Exception as error:
                    caught_exception = error
            else:
                oauth_callback.handler.do_GET(callback)

        return {
            "callback": callback,
            "config_store": config_store,
            "exception": caught_exception,
            "exchange": exchange,
            "identity": identity,
            "logger": logger,
            "preflight": preflight,
            "state": state,
            "token_store": token_store,
            "verifier": verifier,
        }

    def _assert_single_callback_failure_log(
        self,
        result,
        expected_code,
        *,
        inbox_position=None,
        expect_response=True,
    ):
        logger = result["logger"]
        logger.warning.assert_called_once()
        log_call = logger.warning.call_args
        self.assertEqual(log_call.kwargs, {})
        expected_log = (
            "event=gmail_oauth_callback_failure "
            f"failure_code={expected_code} provider=google"
        )
        if inbox_position is not None:
            expected_log += f" inbox_position={inbox_position}"
        self.assertEqual(log_call.args, (expected_log,))
        self.assertIn(expected_code, oauth_callback.GMAIL_CALLBACK_FAILURE_CODES)
        self.assertEqual(logger.mock_calls, [call.warning(expected_log)])

        callback = result["callback"]
        if not expect_response:
            callback._send_callback_page.assert_not_called()
            return None

        callback._send_callback_page.assert_called_once()
        payload = callback._send_callback_page.call_args.args[0]
        serialized_payload = json.dumps(payload, sort_keys=True)
        self.assertNotIn("failure_code", serialized_payload)
        for failure_code in oauth_callback.GMAIL_CALLBACK_FAILURE_CODES:
            self.assertNotIn(failure_code, serialized_payload)
        return payload

    def test_gmail_callback_early_failure_logging_matrix(self):
        with patch.object(connect_oauth.time, "time", return_value=1_000):
            expired_state, _ = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "state-secret",
                "main",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )

        cases = (
            {
                "name": "member_unauthenticated",
                "expected_code": "member_unauthenticated",
                "expected_message": (
                    "Mailbox authentication session could not be verified. "
                    "Please try again."
                ),
                "kwargs": {"member_result": (None, ())},
                "inbox_position": None,
            },
            {
                "name": "state_invalid",
                "expected_code": "state_invalid",
                "expected_message": (
                    "Google authentication could not be verified. Please try again."
                ),
                "kwargs": {"state": "CANARY_INVALID_SIGNED_STATE"},
                "inbox_position": None,
            },
            {
                "name": "state_expired",
                "expected_code": "state_expired",
                "expected_message": (
                    "Google authentication could not be verified. Please try again."
                ),
                "kwargs": {
                    "state": expired_state,
                    "callback_time": 1_901,
                },
                "inbox_position": None,
            },
            {
                "name": "owner_binding_invalid",
                "expected_code": "owner_binding_invalid",
                "expected_message": (
                    "Google authentication session could not be verified. "
                    "Please try again."
                ),
                "kwargs": {
                    "member_result": (
                        authenticated_member("other@example.com"),
                        (),
                    )
                },
                "inbox_position": "main",
            },
            {
                "name": "canonical_origin_invalid",
                "expected_code": "canonical_origin_invalid",
                "expected_message": (
                    "Mailbox authentication could not be completed because the "
                    "application is not configured safely."
                ),
                "kwargs": {
                    "environment": self._production_oauth_environment(
                        CUEVION_APP_URL="https://attacker.example"
                    )
                },
                "inbox_position": "main",
            },
            {
                "name": "provider_denied",
                "expected_code": "provider_denied",
                "expected_message": (
                    "Google authentication was cancelled or denied."
                ),
                "kwargs": {"provider_error": "CANARY_PROVIDER_DENIAL_BODY"},
                "inbox_position": "main",
            },
            {
                "name": "authorization_code_missing",
                "expected_code": "authorization_code_missing",
                "expected_message": (
                    "Google did not return an authorization code."
                ),
                "kwargs": {"authorization_code": None},
                "inbox_position": "main",
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]):
                result = self._run_logged_google_callback(**case["kwargs"])
                payload = self._assert_single_callback_failure_log(
                    result,
                    case["expected_code"],
                    inbox_position=case["inbox_position"],
                )
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["provider"], "google")
                self.assertEqual(payload["message"], case["expected_message"])
                result["exchange"].assert_not_called()

    def test_member_authority_unavailable_logs_one_distinct_safe_code(self):
        callback = self._google_callback("CANARY_UNUSED_STATE")
        callback.headers = HeaderMap(
            {"cookie": "CANARY_MEMBER_AUTHORITY_COOKIE"}
        )
        logger = Mock()
        exception_detail = "CANARY_MEMBER_AUTHORITY_EXCEPTION_DETAIL"

        with patch.dict(
            oauth_callback.os.environ,
            self._production_oauth_environment(),
            clear=True,
        ), patch.object(
            oauth_callback,
            "_GMAIL_CALLBACK_LOGGER",
            logger,
        ), patch.object(
            oauth_callback.http,
            "snapshot_request_headers",
            return_value={"cookie": "CANARY_MEMBER_AUTHORITY_COOKIE"},
        ), patch.object(
            oauth_callback.runtime,
            "resolve_authenticated_member",
            side_effect=RuntimeError(exception_detail),
        ):
            oauth_callback.handler.do_GET(callback)

        result = {"callback": callback, "logger": logger}
        payload = self._assert_single_callback_failure_log(
            result,
            "member_authority_unavailable",
        )
        self.assertEqual(
            payload["message"],
            "Mailbox authentication session could not be verified. Please try again.",
        )
        logged = logger.warning.call_args.args[0]
        self.assertNotIn(exception_detail, logged)
        self.assertNotIn("CANARY_MEMBER_AUTHORITY_COOKIE", logged)

    def test_gmail_callback_provider_and_storage_failure_logging_matrix(self):
        diagnostic_field = oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD
        link_message = (
            "This Gmail inbox could not be linked to the selected onboarding inbox."
        )
        token_storage_message = (
            "Google authentication completed, but secure authorization storage "
            "is unavailable."
        )
        config_storage_message = (
            "Google authentication completed, but the Gmail inbox could not be "
            "saved securely."
        )
        cases = (
            {
                "name": "token_exchange_failed",
                "expected_code": "token_exchange_failed",
                "expected_message": (
                    "Google authentication could not be completed. Please try again."
                ),
                "kwargs": {
                    "exchange_result": (
                        None,
                        {
                            "code": "token_exchange_failed",
                            "message": "CANARY_TOKEN_EXCHANGE_PROVIDER_BODY",
                        },
                    )
                },
            },
            {
                "name": "token_exchange_unavailable",
                "expected_code": "token_exchange_unavailable",
                "expected_message": (
                    "Google authentication could not be completed. Please try again."
                ),
                "kwargs": {
                    "exchange_result": (
                        None,
                        {
                            "code": "token_exchange_unavailable",
                            "message": "CANARY_TOKEN_EXCHANGE_NETWORK_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "token_payload_invalid",
                "expected_code": "token_payload_invalid",
                "expected_message": (
                    "Google returned an incomplete token response."
                ),
                "kwargs": {"exchange_result": ({"expires_in": 3600}, None)},
            },
            {
                "name": "google_identity_invalid",
                "expected_code": "google_identity_invalid",
                "expected_message": (
                    "Google account identity could not be verified. Please try again."
                ),
                "kwargs": {
                    "identity_result": (
                        None,
                        {"code": "google_identity_invalid"},
                    )
                },
            },
            {
                "name": "google_identity_unavailable",
                "expected_code": "google_identity_unavailable",
                "expected_message": (
                    "Google account identity could not be verified. Please try again."
                ),
                "kwargs": {
                    "identity_result": (
                        None,
                        {"code": "google_identity_unavailable"},
                    )
                },
            },
            {
                "name": "gmail_link_conflict",
                "expected_code": "gmail_link_conflict",
                "expected_message": link_message,
                "kwargs": {
                    "preflight_result": (
                        None,
                        {
                            "code": "gmail_link_conflict",
                            "message": "CANARY_GMAIL_LINK_CONFLICT_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "user_config_store_unavailable",
                "expected_code": "user_config_store_unavailable",
                "expected_message": link_message,
                "kwargs": {
                    "preflight_result": (
                        None,
                        {
                            "code": "user_config_store_unavailable",
                            "message": "CANARY_CONFIG_STORE_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "user_config_invalid",
                "expected_code": "user_config_invalid",
                "expected_message": link_message,
                "kwargs": {
                    "preflight_result": (
                        None,
                        {
                            "code": "user_config_persistence_failed",
                            "message": "CANARY_INVALID_CONFIG_DETAIL",
                            diagnostic_field: "user_config_invalid",
                        },
                    )
                },
            },
            {
                "name": "user_config_preflight_failed",
                "expected_code": "user_config_preflight_failed",
                "expected_message": link_message,
                "kwargs": {
                    "preflight_result": (
                        None,
                        {
                            "code": "user_config_persistence_failed",
                            "message": "CANARY_CONFIG_PREFLIGHT_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "token_owner_conflict",
                "expected_code": "token_owner_conflict",
                "expected_message": token_storage_message,
                "kwargs": {
                    "persistence_result": (
                        None,
                        {
                            "code": "token_owner_conflict",
                            "message": "CANARY_TOKEN_OWNER_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "token_persistence_failed",
                "expected_code": "token_persistence_failed",
                "expected_message": token_storage_message,
                "kwargs": {
                    "persistence_result": (
                        None,
                        {
                            "code": "token_persistence_failed",
                            "message": "CANARY_TOKEN_WRITE_DETAIL",
                        },
                    )
                },
            },
            {
                "name": "mailbox_readback_verification_failed",
                "expected_code": "mailbox_readback_verification_failed",
                "expected_message": (
                    "Google authentication completed. Tokens are stored only in "
                    "the current server runtime. Final mailbox activation requires "
                    "durable secure mailbox token storage."
                ),
                "kwargs": {"persistence_result": (None, None)},
            },
            {
                "name": "token_store_unavailable",
                "expected_code": "token_store_unavailable",
                "expected_message": (
                    "Google authentication completed. Tokens are stored only in "
                    "the current server runtime bridge. Final mailbox activation "
                    "requires durable secure mailbox token storage."
                ),
                "kwargs": {
                    "persistence_result": (
                        {"_storage_durable": False},
                        None,
                    )
                },
            },
            {
                "name": "user_config_write_failed",
                "expected_code": "user_config_write_failed",
                "expected_message": config_storage_message,
                "kwargs": {
                    "registration_result": (
                        None,
                        {
                            "code": "user_config_persistence_failed",
                            "message": "CANARY_CONFIG_WRITE_DETAIL",
                            diagnostic_field: "user_config_write_failed",
                        },
                    )
                },
            },
            {
                "name": "user_config_readback_failed",
                "expected_code": "user_config_readback_failed",
                "expected_message": config_storage_message,
                "kwargs": {
                    "registration_result": (
                        None,
                        {
                            "code": "user_config_persistence_failed",
                            "message": "CANARY_CONFIG_READBACK_DETAIL",
                            diagnostic_field: "user_config_readback_failed",
                        },
                    )
                },
            },
        )

        for case in cases:
            with self.subTest(case=case["name"]):
                result = self._run_logged_google_callback(**case["kwargs"])
                payload = self._assert_single_callback_failure_log(
                    result,
                    case["expected_code"],
                    inbox_position="main",
                )
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["provider"], "google")
                self.assertEqual(payload["message"], case["expected_message"])

    def test_unexpected_callback_exception_logs_only_safe_fallback_and_reraises(self):
        exception_detail = "CANARY_UNEXPECTED_EXCEPTION_DETAIL"
        result = self._run_logged_google_callback(
            exchange_side_effect=RuntimeError(exception_detail),
            capture_exception=True,
        )

        self.assertIsInstance(result["exception"], RuntimeError)
        self.assertEqual(str(result["exception"]), exception_detail)
        self._assert_single_callback_failure_log(
            result,
            "unexpected_callback_failure",
            inbox_position="main",
            expect_response=False,
        )
        logged = result["logger"].warning.call_args.args[0]
        self.assertNotIn(exception_detail, logged)
        self.assertNotIn("RuntimeError", logged)

    def test_response_exception_does_not_duplicate_controlled_failure_log(self):
        exception_detail = "CANARY_RESPONSE_EXCEPTION_DETAIL"
        result = self._run_logged_google_callback(
            member_result=(None, ()),
            response_side_effect=RuntimeError(exception_detail),
            capture_exception=True,
        )

        self.assertIsInstance(result["exception"], RuntimeError)
        payload = self._assert_single_callback_failure_log(
            result,
            "member_unauthenticated",
        )
        self.assertEqual(payload["status"], "error")
        logged = result["logger"].warning.call_args.args[0]
        self.assertNotIn(exception_detail, logged)
        self.assertNotIn("unexpected_callback_failure", logged)

    def test_logger_exception_does_not_change_callback_failure_response(self):
        exception_detail = "CANARY_LOGGER_EXCEPTION_DETAIL"
        result = self._run_logged_google_callback(
            member_result=(None, ()),
            logger_side_effect=RuntimeError(exception_detail),
        )

        payload = self._assert_single_callback_failure_log(
            result,
            "member_unauthenticated",
        )
        self.assertIsNone(result["exception"])
        self.assertEqual(
            payload,
            {
                "status": "error",
                "provider": "google",
                "message": (
                    "Mailbox authentication session could not be verified. "
                    "Please try again."
                ),
            },
        )

    def test_successful_gmail_callback_logs_no_failure_event(self):
        result = self._run_logged_google_callback()

        self.assertIsNone(result["exception"])
        self.assertEqual(result["logger"].mock_calls, [])
        result["callback"]._send_callback_page.assert_called_once()
        payload = result["callback"]._send_callback_page.call_args.args[0]
        self.assertEqual(
            payload,
            {
                "status": "success",
                "provider": "google",
                "inboxPosition": "main",
                "email": "verified@gmail.com",
                "mailboxId": "gmail-verified",
                "message": (
                    "Google account connected. Durable mailbox token storage is active."
                ),
            },
        )
        for failure_code in oauth_callback.GMAIL_CALLBACK_FAILURE_CODES:
            self.assertNotIn(failure_code, json.dumps(payload, sort_keys=True))

    def test_log_helper_maps_non_allowlisted_values_to_safe_fallback(self):
        logger = Mock()
        dynamic_value = "CANARY_DYNAMIC_FAILURE_VALUE"
        unsafe_position = "CANARY_UNSAFE_INBOX_POSITION"

        with patch.object(oauth_callback, "_GMAIL_CALLBACK_LOGGER", logger):
            oauth_callback._log_gmail_callback_failure(
                dynamic_value,
                unsafe_position,
            )

        logger.warning.assert_called_once_with(
            "event=gmail_oauth_callback_failure "
            "failure_code=unexpected_callback_failure provider=google"
        )
        rendered = logger.warning.call_args.args[0]
        self.assertNotIn(dynamic_value, rendered)
        self.assertNotIn(unsafe_position, rendered)

    def test_callback_failure_logs_and_browser_response_exclude_canaries(self):
        from urllib.parse import urlencode

        owner_email = "owner-log-canary@example.invalid"
        mailbox_email = "verified@gmail.com"
        state_secret = "CANARY_STATE_SIGNING_SECRET"
        authorization_code = "CANARY_AUTHORIZATION_CODE"
        access_token = "CANARY_ACCESS_TOKEN"
        refresh_token = "CANARY_REFRESH_TOKEN"
        legacy_access_token = "CANARY_LEGACY_ACCESS_TOKEN_IN_RECORD"
        legacy_refresh_token = "CANARY_LEGACY_REFRESH_TOKEN_IN_RECORD"
        token_record_key = "CANARY_DURABLE_TOKEN_RECORD_KEY"
        client_secret = "CANARY_GOOGLE_CLIENT_SECRET"
        request_cookie = "CANARY_REQUEST_COOKIE"
        provider_body = "CANARY_PROVIDER_RESPONSE_BODY"
        token_reference = "CANARY_TOKEN_REFERENCE"
        config_payload = "CANARY_FULL_CONFIG_PAYLOAD"
        mailbox_id = "CANARY_MAILBOX_ID"

        state, verifier = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            owner_email,
            state_secret,
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        query = urlencode(
            {
                "code": authorization_code,
                "state": state,
                "providerBody": provider_body,
            }
        )
        request_path = f"/api/inboxes/oauth-callback?{query}"
        full_request_url = f"https://app.cuevion.com{request_path}"

        class CapturingCallback(FakeHandler):
            def __init__(self):
                super().__init__(
                    raw_body=b"",
                    headers={"cookie": request_cookie},
                )
                self.path = request_path
                self.callback_payload = None

            def _send_callback_page(self, payload, *, set_cookies=()):
                self.callback_payload = payload
                oauth_callback.handler._send_callback_page(
                    self,
                    payload,
                    set_cookies=set_cookies,
                )

        callback = CapturingCallback()
        logger = Mock()
        registration_error = {
            "code": "user_config_persistence_failed",
            "message": provider_body,
            oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD: (
                "user_config_write_failed"
            ),
            "config": {"private": config_payload},
            "mailboxId": mailbox_id,
        }
        environment = self._production_oauth_environment(
            CUEVION_OAUTH_STATE_SECRET=state_secret,
            GOOGLE_CLIENT_SECRET=client_secret,
        )

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=True,
        ), patch.object(
            oauth_callback,
            "_GMAIL_CALLBACK_LOGGER",
            logger,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(owner_email), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_reference": token_reference,
                },
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": mailbox_email, "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
            return_value=({"prepared": True}, None),
        ), patch.object(
            oauth_callback,
            "persist_google_token_record",
            return_value=(
                {
                    "_storage_durable": True,
                    "access_token": legacy_access_token,
                    "refresh_token": legacy_refresh_token,
                    "owner_email": owner_email,
                    "_store_key": token_record_key,
                },
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
            return_value=(None, registration_error),
        ):
            oauth_callback.handler.do_GET(callback)

        result = {"callback": callback, "logger": logger}
        logger.warning.assert_called_once_with(
            "event=gmail_oauth_callback_failure "
            "failure_code=user_config_write_failed provider=google "
            "inbox_position=main"
        )
        self.assertEqual(callback.status, 200)
        self.assertEqual(callback.callback_payload["status"], "error")
        self.assertEqual(
            callback.callback_payload["message"],
            "Google authentication completed, but the Gmail inbox could not be saved securely.",
        )
        response_body = callback.wfile.getvalue().decode("utf-8")
        response_headers = json.dumps(callback.response_headers)
        serialized_payload = json.dumps(callback.callback_payload, sort_keys=True)
        log_output = logger.warning.call_args.args[0]
        combined_artifacts = "\n".join(
            (log_output, response_body, response_headers, serialized_payload)
        )

        canaries = (
            authorization_code,
            state,
            verifier,
            access_token,
            refresh_token,
            legacy_access_token,
            legacy_refresh_token,
            token_record_key,
            request_cookie,
            client_secret,
            state_secret,
            provider_body,
            token_reference,
            config_payload,
            mailbox_id,
            owner_email,
            full_request_url,
        )
        for canary in canaries:
            with self.subTest(canary=canary):
                self.assertNotIn(canary, combined_artifacts)

        self.assertNotIn(mailbox_email, log_output)
        self.assertNotIn("failure_code", serialized_payload)
        self.assertNotIn("failure_code", response_body)
        for failure_code in oauth_callback.GMAIL_CALLBACK_FAILURE_CODES:
            self.assertNotIn(failure_code, serialized_payload)
            self.assertNotIn(failure_code, response_body)

    def test_connect_oauth_requires_session_and_signed_state_has_owner(self):
        handler = FakeHandler({"provider": "google", "email": "hint@gmail.com"})
        with patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            return_value=(
                None,
                {
                    "code": "invalid_session",
                    "message": "The authenticated session is invalid.",
                },
            ),
        ), patch.object(
            connect_oauth,
            "build_signed_state",
        ) as state_builder:
            connect_oauth.handler.do_POST(handler)
        self.assertEqual(handler.status, 401)
        self.assertEqual(handler.payload()["error"]["code"], "unauthorized")
        state_builder.assert_not_called()

        state, verifier = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "Owner@Example.com",
            "state-secret",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        encoded = state.split(".", 1)[0]
        padded = encoded + "=" * (-len(encoded) % 4)
        import base64
        payload = json.loads(base64.urlsafe_b64decode(padded))
        self.assertNotIn("owner_email", payload)
        self.assertNotIn("owner@example.com", json.dumps(payload))
        self.assertNotIn("user-1", json.dumps(payload))
        self.assertNotIn("workspace-1", json.dumps(payload))
        self.assertIsInstance(payload["owner_binding"], str)
        self.assertEqual(payload["email_hint"], "hint@gmail.com")
        self.assertNotIn("code_verifier", payload)
        verified, error = oauth_callback.verify_signed_state(state, "state-secret")
        self.assertIsNone(error)
        self.assertEqual(verified["code_verifier"], verifier)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                verified,
                " OWNER@EXAMPLE.COM ",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )
        self.assertFalse(
            oauth_callback.verify_owner_binding(
                verified,
                "other@example.com",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )

    def test_connect_oauth_maps_authority_unavailability_before_state(self):
        handler = FakeHandler(
            {"provider": "google", "email": "hint@gmail.com"},
            headers=self._authenticated_headers(),
        )
        with patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            return_value=(
                None,
                {
                    "code": "session_auth_unavailable",
                    "message": "Authenticated session validation is unavailable.",
                },
            ),
        ), patch.object(
            connect_oauth,
            "build_signed_state",
        ) as state_builder:
            connect_oauth.handler.do_POST(handler)

        self.assertEqual(handler.status, 503)
        self.assertEqual(
            handler.payload()["error"]["code"],
            "session_auth_unavailable",
        )
        state_builder.assert_not_called()

    def test_config_upsert_rejects_noncanonical_owner_before_storage_resolution(self):
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
        ) as store_config, patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
        ) as storage_read:
            error = oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                authenticated_member("canonical@example.com"),
                email="verified@gmail.com",
                display_name="Verified",
                owner_email="other@example.com",
                message="Connected",
            )
        self.assertEqual(error["code"], "unauthorized")
        store_config.assert_not_called()
        storage_read.assert_not_called()

    def test_authenticated_oauth_start_uses_real_server_authority_and_opaque_state(self):
        headers = self._authenticated_headers()
        request = FakeHandler(
            {"provider": "google", "email": "hint@gmail.com"},
            headers=headers,
        )
        member = authenticated_member()
        authenticated = auth_runtime.AuthenticatedMemberResolution(
            auth_runtime.MemberResolutionOutcome.AUTHENTICATED,
            member,
        )
        environment = {
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
        }
        with patch.dict(connect_oauth.os.environ, environment, clear=False), patch.object(
            user_config_store.auth_runtime,
            "resolve_authenticated_member",
            return_value=authenticated,
        ) as resolver:
            public_user, public_error = user_config_store.resolve_authenticated_user(
                headers
            )
            resolver.reset_mock()
            connect_oauth.handler.do_POST(request)

        self.assertIsNone(public_error)
        self.assertEqual(
            public_user,
            {
                "email": "owner@example.com",
                "name": "Owner",
                "userType": "member",
            },
        )
        resolver.assert_called_once()
        self.assertEqual(request.status, 200)
        response_payload = request.payload()
        self.assertTrue(response_payload["ok"])
        self.assertEqual(
            response_payload["connectionStatus"],
            "waiting_for_authentication",
        )
        authorization_url = response_payload["authorizationUrl"]
        from urllib.parse import parse_qs, urlparse

        authorization_params = parse_qs(urlparse(authorization_url).query)
        self.assertEqual(authorization_params["code_challenge_method"], ["S256"])
        self.assertEqual(len(authorization_params["code_challenge"][0]), 43)
        state = authorization_params["state"][0]
        verified_state, state_error = oauth_callback.verify_signed_state(
            state,
            "state-secret",
        )
        self.assertIsNone(state_error)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                verified_state,
                member.email,
                "state-secret",
                member_user_id=member.user_id,
                member_workspace_id=member.workspace_id,
            )
        )
        encoded = state.split(".", 1)[0]
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertNotIn("owner_email", decoded)
        self.assertNotIn("owner@example.com", json.dumps(decoded))
        self.assertNotIn(member.user_id, json.dumps(decoded))
        self.assertNotIn(member.workspace_id, json.dumps(decoded))
        self.assertNotIn("code_verifier", decoded)

    def test_failed_existing_gmail_reconnect_builds_fresh_urls_without_old_token(self):
        from urllib.parse import parse_qs, urlparse

        existing_mailbox = inbox(
            connected=False,
            connectionStatus="connection_failed",
        )
        request_payload = {
            "provider": existing_mailbox["provider"],
            "email": existing_mailbox["email"],
        }
        requests = [
            FakeHandler(request_payload, headers=self._authenticated_headers()),
            FakeHandler(request_payload, headers=self._authenticated_headers()),
        ]
        member = authenticated_member()

        with patch.dict(
            os.environ,
            self._production_oauth_environment(),
            clear=True,
        ), patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            return_value=(member, None),
        ), patch.object(
            connect_oauth,
            "resolve_user_config_store",
        ) as config_store, patch.object(
            connect_oauth,
            "read_user_config_record",
        ) as config_read, patch.object(
            oauth_token_store,
            "load_google_token_record_with_metadata",
        ) as token_read, patch(
            "urllib.request.urlopen",
        ) as network_request, patch.object(
            connect_oauth.secrets,
            "token_urlsafe",
            side_effect=("fresh-reconnect-nonce-one", "fresh-reconnect-nonce-two"),
        ):
            for request in requests:
                connect_oauth.handler.do_POST(request)

        authorization_urls = [
            request.payload()["authorizationUrl"] for request in requests
        ]
        states = [
            parse_qs(urlparse(authorization_url).query)["state"][0]
            for authorization_url in authorization_urls
        ]
        self.assertEqual([request.status for request in requests], [200, 200])
        self.assertEqual(
            [
                request.payload()["connectionStatus"]
                for request in requests
            ],
            ["waiting_for_authentication", "waiting_for_authentication"],
        )
        self.assertNotEqual(authorization_urls[0], authorization_urls[1])
        self.assertNotEqual(states[0], states[1])
        for state in states:
            verified_state, state_error = oauth_callback.verify_signed_state(
                state,
                "state-secret",
            )
            self.assertIsNone(state_error)
            self.assertTrue(
                oauth_callback.verify_owner_binding(
                    verified_state,
                    member.email,
                    "state-secret",
                    member_user_id=member.user_id,
                    member_workspace_id=member.workspace_id,
                )
            )
        self.assertEqual(
            existing_mailbox["connectionStatus"],
            "connection_failed",
        )
        config_store.assert_not_called()
        config_read.assert_not_called()
        token_read.assert_not_called()
        network_request.assert_not_called()

    def test_google_start_and_exchange_share_canonical_production_redirect_uri(self):
        from urllib.parse import parse_qs, urlencode, urlparse

        expected_redirect_uri = (
            "https://app.cuevion.com/api/inboxes/oauth-callback"
        )
        environment = self._production_oauth_environment(
            CUEVION_APP_URL="https://app.cuevion.com/",
            GOOGLE_OAUTH_REDIRECT_URI=(
                "https://cuevion.vercel.app/api/inboxes/oauth-callback"
            ),
            VERCEL_URL="cuevion.vercel.app",
        )
        hostile_headers = self._authenticated_headers()
        hostile_headers.update(
            {
                "host": "attacker.example",
                "x-forwarded-host": "preview-attacker.vercel.app",
                "x-forwarded-proto": "http",
            }
        )
        request = FakeHandler(
            {"provider": "google", "email": "hint@gmail.com"},
            headers=hostile_headers,
        )

        with patch.dict(os.environ, environment, clear=True), patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            side_effect=resolve_test_member_authority,
        ):
            connect_oauth.handler.do_POST(request)

            self.assertEqual(request.status, 200)
            authorization_url = request.payload()["authorizationUrl"]
            authorization_params = parse_qs(urlparse(authorization_url).query)
            self.assertEqual(
                authorization_params["redirect_uri"],
                [expected_redirect_uri],
            )
            self.assertEqual(
                authorization_params["code_challenge_method"],
                ["S256"],
            )

            state = authorization_params["state"][0]
            verified_state, state_error = oauth_callback.verify_signed_state(
                state,
                "state-secret",
            )
            self.assertIsNone(state_error)
            self.assertEqual(
                authorization_params["code_challenge"],
                [
                    connect_oauth.build_code_challenge(
                        verified_state["code_verifier"]
                    )
                ],
            )
            self.assertNotIn(
                verified_state["code_verifier"],
                authorization_url,
            )

            callback = Mock()
            callback_query = urlencode(
                {
                    "code": "provider-code",
                    "state": state,
                    "returnTo": "https://attacker.example/steal",
                    "origin": "https://attacker.example",
                    "redirect_uri": "https://attacker.example/callback",
                }
            )
            callback.path = f"/api/inboxes/oauth-callback?{callback_query}"
            callback.headers = HeaderMap(hostile_headers)
            callback._send_callback_page = Mock()
            with patch.object(
                oauth_callback,
                "_resolve_authenticated_member_request",
                return_value=(authenticated_member(), ()),
            ), patch.object(
                oauth_callback,
                "_exchange_google_code",
                return_value=(
                    None,
                    {
                        "code": "token_exchange_failed",
                        "message": "Mocked provider failure.",
                    },
                ),
            ) as exchange:
                oauth_callback.handler.do_GET(callback)

        exchange.assert_called_once_with(
            code="provider-code",
            code_verifier=verified_state["code_verifier"],
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri=expected_redirect_uri,
        )
        self.assertEqual(
            callback._callback_app_redirect_url,
            "https://app.cuevion.com/",
        )
        callback_payload = callback._send_callback_page.call_args.args[0]
        serialized_payload = json.dumps(callback_payload)
        self.assertNotIn(state, serialized_payload)
        for forbidden in (
            "provider-code",
            "state-secret",
            "client-secret",
            "code_verifier",
            "attacker.example",
            "cuevion.vercel.app",
        ):
            self.assertNotIn(forbidden, serialized_payload)

    def test_oauth_start_rejects_browser_selected_origins_and_redirect_targets(self):
        for field in ("origin", "returnTo", "redirect_uri"):
            with self.subTest(field=field):
                request = FakeHandler(
                    {
                        "provider": "google",
                        "email": "hint@gmail.com",
                        field: "https://attacker.example/redirect",
                    },
                    headers=self._authenticated_headers(),
                )
                with patch.dict(
                    os.environ,
                    self._production_oauth_environment(),
                    clear=True,
                ), patch.object(
                    connect_oauth,
                    "resolve_authenticated_member_authority",
                    side_effect=resolve_test_member_authority,
                ), patch.object(
                    connect_oauth,
                    "build_signed_state",
                ) as state_builder:
                    connect_oauth.handler.do_POST(request)

                self.assertEqual(request.status, 400)
                self.assertEqual(
                    request.payload()["error"]["code"],
                    "invalid_request",
                )
                state_builder.assert_not_called()

    def test_invalid_or_missing_production_origin_fails_before_oauth_state(self):
        invalid_origins = (
            ("missing", None),
            ("http", "http://app.cuevion.com"),
            ("preview", "https://cuevion.vercel.app"),
            ("path", "https://app.cuevion.com/oauth"),
            ("double_slash_path", "https://app.cuevion.com//"),
            ("query", "https://app.cuevion.com?returnTo=/inbox"),
            ("fragment", "https://app.cuevion.com#callback"),
            ("userinfo", "https://user:password@app.cuevion.com"),
            ("wildcard", "https://*.cuevion.com"),
            ("port", "https://app.cuevion.com:443"),
        )

        for label, configured_origin in invalid_origins:
            with self.subTest(label=label):
                environment = self._production_oauth_environment(
                    GOOGLE_OAUTH_REDIRECT_URI=(
                        "https://cuevion.vercel.app/api/inboxes/oauth-callback"
                    ),
                    VERCEL_URL="cuevion.vercel.app",
                )
                if configured_origin is None:
                    environment.pop("CUEVION_APP_URL")
                else:
                    environment["CUEVION_APP_URL"] = configured_origin
                headers = self._authenticated_headers()
                headers.update(
                    {
                        "host": "app.cuevion.com",
                        "x-forwarded-host": "cuevion.vercel.app",
                        "x-forwarded-proto": "https",
                    }
                )
                request = FakeHandler(
                    {"provider": "google", "email": "hint@gmail.com"},
                    headers=headers,
                )
                with patch.dict(os.environ, environment, clear=True), patch.object(
                    connect_oauth,
                    "resolve_authenticated_member_authority",
                    side_effect=resolve_test_member_authority,
                ), patch.object(
                    connect_oauth,
                    "build_signed_state",
                ) as state_builder:
                    connect_oauth.handler.do_POST(request)

                self.assertEqual(request.status, 503)
                self.assertEqual(
                    request.payload()["error"]["code"],
                    "oauth_public_origin_invalid",
                )
                state_builder.assert_not_called()

    def test_local_origin_fallback_is_limited_to_exact_loopback_hosts(self):
        with patch.dict(os.environ, {}, clear=True):
            for module in (connect_oauth, oauth_callback):
                with self.subTest(module=module.__name__, host="localhost"):
                    self.assertEqual(
                        module.resolve_public_app_origin(
                            HeaderMap({"host": "localhost:3000"})
                        ),
                        "http://localhost:3000",
                    )
                with self.subTest(module=module.__name__, host="ipv4"):
                    self.assertEqual(
                        module.resolve_public_app_origin(
                            HeaderMap(
                                {
                                    "host": "127.0.0.1:5173",
                                    "x-forwarded-proto": "http",
                                }
                            )
                        ),
                        "http://127.0.0.1:5173",
                    )
                for invalid_host in (
                    "localhost.attacker.example",
                    "cuevion.vercel.app",
                    "app.cuevion.com",
                ):
                    with self.subTest(
                        module=module.__name__,
                        host=invalid_host,
                    ):
                        self.assertIsNone(
                            module.resolve_public_app_origin(
                                HeaderMap({"host": invalid_host})
                            )
                        )
                with self.subTest(module=module.__name__, host="forwarded"):
                    self.assertIsNone(
                        module.resolve_public_app_origin(
                            HeaderMap(
                                {
                                    "host": "internal.invalid",
                                    "x-forwarded-host": "localhost:5173",
                                    "x-forwarded-proto": "http",
                                }
                            )
                        )
                    )

        preview_environment = {
            "VERCEL_ENV": "preview",
            "CUEVION_APP_URL": "https://explicit-preview.vercel.app",
        }
        with patch.dict(os.environ, preview_environment, clear=True):
            for module in (connect_oauth, oauth_callback):
                self.assertEqual(
                    module.resolve_public_app_origin(HeaderMap()),
                    "https://explicit-preview.vercel.app",
                )

        for preview_loopback in (
            "https://localhost",
            "https://127.0.0.1",
            "https://[::1]",
        ):
            with patch.dict(
                os.environ,
                {
                    "VERCEL_ENV": "preview",
                    "CUEVION_APP_URL": preview_loopback,
                },
                clear=True,
            ):
                for module in (connect_oauth, oauth_callback):
                    self.assertIsNone(
                        module.resolve_public_app_origin(HeaderMap())
                    )

        missing_preview_origin_environment = {
            "VERCEL_ENV": "preview",
            "VERCEL_URL": "implicit-preview.vercel.app",
            "GOOGLE_OAUTH_REDIRECT_URI": (
                "https://implicit-preview.vercel.app/api/inboxes/oauth-callback"
            ),
        }
        preview_headers = HeaderMap(
            {
                "host": "implicit-preview.vercel.app",
                "x-forwarded-host": "localhost:3000",
                "x-forwarded-proto": "https",
            }
        )
        with patch.dict(
            os.environ,
            missing_preview_origin_environment,
            clear=True,
        ):
            for module in (connect_oauth, oauth_callback):
                self.assertIsNone(
                    module.resolve_public_app_origin(preview_headers)
                )

        with patch.dict(
            os.environ,
            {"CUEVION_APP_URL": "https://unclassified.example"},
            clear=True,
        ):
            for module in (connect_oauth, oauth_callback):
                self.assertIsNone(
                    module.resolve_public_app_origin(HeaderMap())
                )

    def test_invalid_production_origin_stops_callback_before_provider_or_storage(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        invalid_origins = (
            ("missing", None),
            ("http", "http://app.cuevion.com"),
            ("path", "https://app.cuevion.com/callback"),
            ("query", "https://app.cuevion.com?next=/"),
            ("fragment", "https://app.cuevion.com#next"),
            ("userinfo", "https://user@app.cuevion.com"),
        )

        for label, configured_origin in invalid_origins:
            with self.subTest(label=label):
                environment = self._production_oauth_environment(
                    VERCEL_URL="cuevion.vercel.app",
                )
                if configured_origin is None:
                    environment.pop("CUEVION_APP_URL")
                else:
                    environment["CUEVION_APP_URL"] = configured_origin
                callback = self._google_callback(state)
                callback.headers = HeaderMap(
                    {
                        "host": "app.cuevion.com",
                        "x-forwarded-host": "cuevion.vercel.app",
                        "x-forwarded-proto": "https",
                    }
                )

                with patch.dict(os.environ, environment, clear=True), patch.object(
                    oauth_callback,
                    "_resolve_authenticated_member_request",
                    return_value=(authenticated_member(), ()),
                ), patch.object(
                    oauth_callback,
                    "_exchange_google_code",
                ) as exchange, patch.object(
                    oauth_callback,
                    "_fetch_verified_google_identity",
                ) as identity, patch.object(
                    oauth_callback,
                    "_prepare_gmail_managed_inbox_registration",
                ) as preflight, patch.object(
                    oauth_callback,
                    "persist_google_token_record",
                ) as token_store, patch.object(
                    oauth_callback,
                    "_register_gmail_managed_inbox_in_user_config",
                ) as config_store:
                    oauth_callback.handler.do_GET(callback)

                exchange.assert_not_called()
                identity.assert_not_called()
                preflight.assert_not_called()
                token_store.assert_not_called()
                config_store.assert_not_called()
                response = callback._send_callback_page.call_args.args[0]
                self.assertEqual(response["status"], "error")
                self.assertEqual(response["provider"], "google")
                serialized_response = json.dumps(response)
                for forbidden in (
                    "provider-code",
                    "state-secret",
                    "code_verifier",
                    "cuevion.vercel.app",
                ):
                    self.assertNotIn(forbidden, serialized_response)

    def test_callback_bridge_uses_only_canonical_origin_and_fails_closed_without_it(self):
        safe_payload = oauth_callback._build_callback_payload(
            provider="google",
            email="",
            connection_status="connection_failed",
            message="Google authentication could not be completed.",
            connected=False,
        )
        hostile_headers = HeaderMap(
            {
                "host": "attacker.example",
                "x-forwarded-host": "cuevion.vercel.app",
                "x-forwarded-proto": "http",
            }
        )
        configured_handler = FakeHandler(headers=hostile_headers)
        with patch.dict(
            os.environ,
            self._production_oauth_environment(
                VERCEL_URL="cuevion.vercel.app",
            ),
            clear=True,
        ):
            oauth_callback.handler._send_callback_page(
                configured_handler,
                safe_payload,
            )

        configured_page = configured_handler.wfile.getvalue().decode("utf-8")
        self.assertEqual(configured_handler.status, 200)
        self.assertIn(
            ("Referrer-Policy", "no-referrer"),
            configured_handler.response_headers,
        )
        self.assertIn('const redirectUrl = "https://app.cuevion.com/";', configured_page)
        self.assertIn(
            'window.history.replaceState(null, "", "/api/inboxes/oauth-callback")',
            configured_page,
        )
        self.assertIn("window.localStorage.setItem", configured_page)
        self.assertIn("window.location.replace(redirectUrl)", configured_page)
        self.assertNotIn("attacker.example", configured_page)
        self.assertNotIn("cuevion.vercel.app", configured_page)

        unconfigured_handler = FakeHandler(headers=hostile_headers)
        missing_origin_environment = self._production_oauth_environment(
            VERCEL_URL="cuevion.vercel.app",
        )
        missing_origin_environment.pop("CUEVION_APP_URL")
        with patch.dict(os.environ, missing_origin_environment, clear=True):
            oauth_callback.handler._send_callback_page(
                unconfigured_handler,
                safe_payload,
            )

        unconfigured_page = unconfigured_handler.wfile.getvalue().decode("utf-8")
        self.assertEqual(unconfigured_handler.status, 503)
        self.assertIn(
            ("Referrer-Policy", "no-referrer"),
            unconfigured_handler.response_headers,
        )
        self.assertIn(
            'window.history.replaceState(null, "", "/api/inboxes/oauth-callback")',
            unconfigured_page,
        )
        self.assertNotIn("window.localStorage", unconfigured_page)
        self.assertNotIn("window.location.replace", unconfigured_page)
        self.assertNotIn("attacker.example", unconfigured_page)
        self.assertNotIn("cuevion.vercel.app", unconfigured_page)

    def test_onboarding_oauth_start_requires_authoritative_selected_position(self):
        environment = {
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": (
                "https://app.example.com/api/inboxes/oauth-callback"
            ),
        }
        valid_config = self._incomplete_onboarding_config(
            selected_inboxes=("main", "demo")
        )
        valid_config["managedInboxes"] = [
            inbox(
                id="gmail-existing",
                connected=False,
                connectionStatus="connection_failed",
                connectionMethod="oauth",
                connectionType="oauth",
                oauthOwnerEmail="owner@example.com",
                onboardingInboxId="main",
            )
        ]
        request = FakeHandler(
            {
                "provider": "google",
                "email": " Hint@Gmail.com ",
                "inboxPosition": "main",
            },
            headers=self._authenticated_headers(),
        )

        with patch.dict(
            connect_oauth.os.environ,
            environment,
            clear=False,
        ), patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            side_effect=resolve_test_member_authority,
        ), patch.object(
            connect_oauth,
            "resolve_user_config_store",
            return_value=({"rest_url": "https://kv.example", "rest_token": "secret"}, None),
        ), patch.object(
            connect_oauth,
            "read_user_config_record",
            return_value={"status": "ok", "config": valid_config, "error": None},
        ) as config_read:
            connect_oauth.handler.do_POST(request)

        self.assertEqual(request.status, 200)
        config_read.assert_called_once()
        authorization_url = request.payload()["authorizationUrl"]
        from urllib.parse import parse_qs, urlparse

        authorization_params = parse_qs(urlparse(authorization_url).query)
        self.assertEqual(authorization_params["login_hint"], ["hint@gmail.com"])
        state = authorization_params["state"][0]
        verified, error = oauth_callback.verify_signed_state(
            state,
            "state-secret",
        )
        self.assertIsNone(error)
        self.assertEqual(verified["inboxPosition"], "main")
        self.assertEqual(verified["email_hint"], "hint@gmail.com")
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                verified,
                "owner@example.com",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )

    def test_onboarding_oauth_start_rejects_missing_completed_and_unselected_state(self):
        completed_config = self._incomplete_onboarding_config(completed=True)
        unselected_config = self._incomplete_onboarding_config(
            selected_inboxes=("demo",)
        )
        cases = (
            (
                "missing_record",
                {"status": "missing", "config": None, "error": None},
            ),
            (
                "missing_session",
                {
                    "status": "ok",
                    "config": {"v": 1, "email": "owner@example.com"},
                    "error": None,
                },
            ),
            (
                "completed_session",
                {"status": "ok", "config": completed_config, "error": None},
            ),
            (
                "unselected_position",
                {"status": "ok", "config": unselected_config, "error": None},
            ),
        )

        for label, read_result in cases:
            request = FakeHandler(
                {
                    "provider": "google",
                    "email": "hint@gmail.com",
                    "inboxPosition": "main",
                },
                headers=self._authenticated_headers(),
            )
            with self.subTest(label=label), patch.object(
                connect_oauth,
                "resolve_authenticated_member_authority",
                side_effect=resolve_test_member_authority,
            ), patch.object(
                connect_oauth,
                "resolve_user_config_store",
                return_value=(
                    {"rest_url": "https://kv.example", "rest_token": "secret"},
                    None,
                ),
            ), patch.object(
                connect_oauth,
                "read_user_config_record",
                return_value=read_result,
            ), patch.object(
                connect_oauth,
                "build_signed_state",
            ) as state_builder:
                connect_oauth.handler.do_POST(request)

            self.assertEqual(request.status, 409)
            self.assertEqual(
                request.payload()["error"]["code"],
                "onboarding_state_conflict",
            )
            state_builder.assert_not_called()

    def test_onboarding_oauth_start_rejects_every_extra_or_identity_field(self):
        forbidden_fields = {
            "internalRole": "producer",
            "focusPreferences": {"promo": "medium"},
            "selectedInboxes": ["main"],
            "onboardingSession": self._incomplete_onboarding_config()[
                "onboardingSession"
            ],
            "userId": "attacker-user",
            "workspaceId": "attacker-workspace",
            "membershipRole": "attacker-owner",
            "ownerEmail": "attacker@example.com",
            "connected": True,
            "state": "client-state",
            "oauthState": "browser-state",
            "nonce": "client-nonce",
            "pkce": "client-pkce",
            "codeVerifier": "client-verifier",
            "redirectUri": "https://attacker.example/callback",
            "redirect_uri": "https://attacker.example/callback",
            "accessToken": "client-access-token",
            "refreshToken": "client-refresh-token",
        }

        for field, value in forbidden_fields.items():
            request = FakeHandler(
                {
                    "provider": "google",
                    "email": "hint@gmail.com",
                    "inboxPosition": "main",
                    field: value,
                },
                headers=self._authenticated_headers(),
            )
            with self.subTest(field=field), patch.object(
                connect_oauth,
                "resolve_authenticated_member_authority",
                side_effect=resolve_test_member_authority,
            ), patch.object(
                connect_oauth,
                "resolve_user_config_store",
            ) as store_resolver, patch.object(
                connect_oauth,
                "build_signed_state",
            ) as state_builder:
                connect_oauth.handler.do_POST(request)

            self.assertEqual(request.status, 400)
            self.assertEqual(request.payload()["error"]["code"], "invalid_request")
            store_resolver.assert_not_called()
            state_builder.assert_not_called()

    def test_onboarding_oauth_start_rejects_explicit_null_position_before_store(self):
        request = FakeHandler(
            {
                "provider": "google",
                "email": "hint@gmail.com",
                "inboxPosition": None,
            },
            headers=self._authenticated_headers(),
        )
        with patch.object(
            connect_oauth,
            "resolve_authenticated_member_authority",
            side_effect=resolve_test_member_authority,
        ), patch.object(
            connect_oauth,
            "resolve_user_config_store",
        ) as store_resolver, patch.object(
            connect_oauth,
            "read_user_config_record",
        ) as config_read, patch.object(
            connect_oauth,
            "build_signed_state",
        ) as state_builder:
            connect_oauth.handler.do_POST(request)

        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        store_resolver.assert_not_called()
        config_read.assert_not_called()
        state_builder.assert_not_called()

    def test_state_expiry_tampering_context_binding_and_pkce_are_stable(self):
        with patch.object(connect_oauth.time, "time", return_value=1_000), patch.object(
            connect_oauth.secrets,
            "token_urlsafe",
            return_value="fixed-state-nonce-value",
        ):
            state, verifier = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "stable-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        with patch.object(oauth_callback.time, "time", return_value=1_001):
            payload, error = oauth_callback.verify_signed_state(state, "stable-secret")
        self.assertIsNone(error)
        self.assertEqual(payload["code_verifier"], verifier)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                payload,
                " OWNER@EXAMPLE.COM ",
                "stable-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )

        encoded, signature = state.split(".", 1)
        import base64
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        for field, replacement in (
            ("nonce", "substituted-state-nonce"),
            ("email_hint", "other@gmail.com"),
            ("owner_binding", "A" * 43),
        ):
            modified = {**decoded, field: replacement}
            modified_encoded = connect_oauth.base64url_encode(
                json.dumps(modified, separators=(",", ":"), sort_keys=True).encode()
            )
            tampered = f"{modified_encoded}.{signature}"
            with patch.object(oauth_callback.time, "time", return_value=1_001):
                verified, error = oauth_callback.verify_signed_state(
                    tampered, "stable-secret"
                )
            self.assertIsNone(verified)
            self.assertEqual(error, "invalid_state")

        with patch.object(oauth_callback.time, "time", return_value=1_901):
            verified, error = oauth_callback.verify_signed_state(state, "stable-secret")
        self.assertIsNone(verified)
        self.assertEqual(error, "expired_state")

        reloaded_connect = load_route(
            "connect-oauth.py", "connect_oauth_separate_instance_test"
        )
        with patch.object(reloaded_connect.time, "time", return_value=1_000), patch.object(
            reloaded_connect.secrets,
            "token_urlsafe",
            return_value="fixed-state-nonce-value",
        ):
            separate_state, separate_verifier = reloaded_connect.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "stable-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        self.assertEqual(separate_state, state)
        self.assertEqual(separate_verifier, verifier)

    def test_signed_onboarding_position_is_tamper_expiry_and_owner_bound(self):
        with patch.object(connect_oauth.time, "time", return_value=1_000), patch.object(
            connect_oauth.secrets,
            "token_urlsafe",
            return_value="fixed-onboarding-nonce",
        ):
            state, verifier = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "state-secret",
                "main",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )

        with patch.object(oauth_callback.time, "time", return_value=1_001):
            verified, error = oauth_callback.verify_signed_state(
                state,
                "state-secret",
            )
        self.assertIsNone(error)
        self.assertEqual(verified["inboxPosition"], "main")
        self.assertEqual(verified["code_verifier"], verifier)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                verified,
                " OWNER@EXAMPLE.COM ",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )
        self.assertFalse(
            oauth_callback.verify_owner_binding(
                verified,
                "other@example.com",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
        )

        encoded, signature = state.split(".", 1)
        decoded = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        decoded["inboxPosition"] = "demo"
        tampered_encoded = connect_oauth.base64url_encode(
            json.dumps(decoded, separators=(",", ":"), sort_keys=True).encode()
        )
        with patch.object(oauth_callback.time, "time", return_value=1_001):
            tampered_payload, tampered_error = oauth_callback.verify_signed_state(
                f"{tampered_encoded}.{signature}",
                "state-secret",
            )
        self.assertIsNone(tampered_payload)
        self.assertEqual(tampered_error, "invalid_state")

        with patch.object(oauth_callback.time, "time", return_value=1_901):
            expired_payload, expired_error = oauth_callback.verify_signed_state(
                state,
                "state-secret",
            )
        self.assertIsNone(expired_payload)
        self.assertEqual(expired_error, "expired_state")

    def test_historical_beta_cookie_cannot_authorize_oauth_callback(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = Mock()
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state}"
        callback.headers = HeaderMap(
            {"cookie": "cuevion_beta_session=historical-member-cookie"}
        )
        callback._send_callback_page = Mock()
        with patch.dict(
            oauth_callback.os.environ,
            {
                "CUEVION_OAUTH_STATE_SECRET": "state-secret",
                "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
            },
            clear=False,
        ), patch.object(oauth_callback, "_exchange_google_code") as exchange:
            oauth_callback.handler.do_GET(callback)
        exchange.assert_not_called()
        self.assertEqual(
            callback._send_callback_page.call_args.args[0]["status"],
            "error",
        )

    def test_callback_rejects_owner_mismatch_before_token_exchange(self):
        callback = Mock()
        state_value, _ = connect_oauth.build_signed_state(
            "google",
            "attacker@gmail.com",
            "owner@example.com",
            "state-secret",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state_value}"
        callback.headers = HeaderMap()
        callback._send_callback_page = Mock()
        with patch.dict(
            oauth_callback.os.environ,
            {
                "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            },
            clear=False,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member("other@example.com"), ()),
        ), patch.object(oauth_callback, "_exchange_google_code") as exchange:
            oauth_callback.handler.do_GET(callback)
        exchange.assert_not_called()
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["status"], "error")
        self.assertNotIn("email", response)

    def test_callback_uses_verified_email_and_persists_owner(self):
        callback = Mock()
        state_value, _ = connect_oauth.build_signed_state(
            "google",
            "attacker@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state_value}"
        callback.headers = HeaderMap()
        callback._send_callback_page = Mock()
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
        }
        with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(), ()),
        ), patch.object(
            oauth_callback, "_exchange_google_code", return_value=({"access_token": "secret-token"}, None)
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=({"email": "verified@gmail.com", "display_name": "Verified"}, None),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
            return_value=({"prepared": True}, None),
        ) as preflight, patch.object(
            oauth_callback,
            "persist_google_token_record",
            return_value=({"_storage_durable": True}, None),
        ) as persist, patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
            return_value=({"id": "gmail-verified"}, None),
        ) as register:
            oauth_callback.handler.do_GET(callback)
        preflight.assert_called_once_with(
            authenticated_member(),
            email="verified@gmail.com",
            owner_email="owner@example.com",
            inbox_position="main",
        )
        persist.assert_called_once_with(
            email="verified@gmail.com",
            owner_email="owner@example.com",
            token_payload={"access_token": "secret-token"},
        )
        self.assertEqual(register.call_args.kwargs["email"], "verified@gmail.com")
        self.assertEqual(register.call_args.kwargs["owner_email"], "owner@example.com")
        self.assertEqual(register.call_args.kwargs["inbox_position"], "main")
        self.assertEqual(register.call_args.args[0].email, "owner@example.com")
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(
            response,
            {
                "status": "success",
                "provider": "google",
                "inboxPosition": "main",
                "email": "verified@gmail.com",
                "mailboxId": "gmail-verified",
                "message": (
                    "Google account connected. Durable mailbox token storage is active."
                ),
            },
        )
        serialized_response = json.dumps(response).lower()
        for forbidden in (
            "attacker@gmail.com",
            "provider-code",
            "state-secret",
            "secret-token",
            "refresh_token",
            "owner_binding",
            "code_verifier",
        ):
            self.assertNotIn(forbidden, serialized_response)

    def test_missing_verified_google_identity_fails_closed(self):
        class ProviderResponse:
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _limit):
                return json.dumps({"email": "hint@gmail.com", "email_verified": False}).encode()

        with patch.object(oauth_callback, "urlopen", return_value=ProviderResponse()):
            identity, error = oauth_callback._fetch_verified_google_identity("mock-token")
        self.assertIsNone(identity)
        self.assertEqual(error["code"], "google_identity_invalid")

        for invalid_verified in ("true", 1, {}, []):
            class TruthyProviderResponse(BoundaryResponse):
                pass

            response = TruthyProviderResponse(
                json.dumps(
                    {
                        "email": "hint@gmail.com",
                        "email_verified": invalid_verified,
                    }
                )
            )
            with self.subTest(value=invalid_verified), patch.object(
                oauth_callback,
                "urlopen",
                return_value=response,
            ):
                identity, error = oauth_callback._fetch_verified_google_identity(
                    "mock-token"
                )
            self.assertIsNone(identity)
            self.assertEqual(error["code"], "google_identity_invalid")

    def test_callback_rejects_unverified_google_identity_before_any_storage(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = self._google_callback(state)
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
        }

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=False,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=({"access_token": "provider-access-token"}, None),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(None, {"code": "google_identity_invalid"}),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
        ) as preflight, patch.object(
            oauth_callback,
            "persist_google_token_record",
        ) as token_store, patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
        ) as config_store:
            oauth_callback.handler.do_GET(callback)

        preflight.assert_not_called()
        token_store.assert_not_called()
        config_store.assert_not_called()
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["provider"], "google")
        self.assertNotIn("mailboxId", response)
        self.assertNotIn("email", response)
        self.assertNotIn("provider-access-token", json.dumps(response))

    def test_google_callback_payload_has_an_exact_safe_success_contract(self):
        payload = oauth_callback._build_callback_payload(
            provider="google",
            email=" Verified@Gmail.com ",
            connection_status="connected",
            message="Connected",
            connected=True,
            display_name="Verified User",
            inbox_position="main",
            mailbox_id=" gmail-verified ",
        )
        self.assertEqual(
            payload,
            {
                "status": "success",
                "provider": "google",
                "inboxPosition": "main",
                "email": "verified@gmail.com",
                "mailboxId": "gmail-verified",
                "message": "Connected",
            },
        )
        self.assertNotIn("connected", payload)
        self.assertNotIn("connectionStatus", payload)
        self.assertNotIn("displayName", payload)

    def test_existing_google_token_record_classification_is_exact_and_fail_closed(self):
        current_record = durable_google_token()
        legacy_record = {
            key: value
            for key, value in current_record.items()
            if key != "owner_email"
        }
        cases = (
            ("absent", None, oauth_callback.GOOGLE_TOKEN_RECORD_ABSENT),
            (
                "exact_owner",
                current_record,
                oauth_callback.GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH,
            ),
            (
                "exact_owner_existing_minimal_shape",
                {
                    "provider": "google",
                    "email": "verified@gmail.com",
                    "owner_email": "owner@example.com",
                    "access_token": "old-access-token",
                    "refresh_token": "old-refresh-token",
                    "created_at": "2025-01-01T00:00:00+00:00",
                },
                oauth_callback.GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH,
            ),
            (
                "legacy_ownerless",
                legacy_record,
                oauth_callback.GOOGLE_TOKEN_RECORD_LEGACY_OWNERLESS_MATCH,
            ),
            (
                "legacy_owner_equals_mailbox",
                {**current_record, "owner_email": "verified@gmail.com"},
                oauth_callback.GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH,
            ),
            (
                "legacy_owner_equals_mailbox_after_canonicalization",
                {**current_record, "owner_email": " Verified@Gmail.com "},
                oauth_callback.GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH,
            ),
            (
                "wrong_owner",
                {**current_record, "owner_email": "other@example.com"},
                oauth_callback.GOOGLE_TOKEN_RECORD_OWNER_MISMATCH,
            ),
            (
                "wrong_provider",
                {**legacy_record, "provider": "microsoft"},
                oauth_callback.GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH,
            ),
            (
                "wrong_email",
                {**legacy_record, "email": "other@gmail.com"},
                oauth_callback.GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH,
            ),
        )

        for label, record, expected in cases:
            with self.subTest(label=label):
                classification = (
                    oauth_callback._classify_existing_google_token_record(
                        record,
                        normalized_email="verified@gmail.com",
                        normalized_owner_email="owner@example.com",
                    )
                )
            self.assertEqual(classification, expected)

        ambiguous_records = (
            ("non_dict", [legacy_record]),
            ("owner_null", {**current_record, "owner_email": None}),
            ("owner_empty", {**current_record, "owner_email": ""}),
            ("owner_whitespace", {**current_record, "owner_email": "   "}),
            ("owner_wrong_type", {**current_record, "owner_email": []}),
            (
                "owner_noncanonical",
                {**current_record, "owner_email": "Owner@Example.com"},
            ),
            ("partial_owner", {**legacy_record, "owner": "owner@example.com"}),
            (
                "ambiguous_owner_binding",
                {**legacy_record, "owner_binding": "CANARY_OWNER_BINDING"},
            ),
            (
                "matching_owner_with_conflicting_owner_identity",
                {**current_record, "ownerEmail": "other@example.com"},
            ),
            (
                "matching_owner_with_malformed_refresh",
                {**current_record, "refresh_token": []},
            ),
            ("missing_access", {key: value for key, value in legacy_record.items() if key != "access_token"}),
            ("empty_access", {**legacy_record, "access_token": ""}),
            ("invalid_expiry", {**legacy_record, "expires_in": True}),
            (
                "expiry_missing_timestamp",
                {**legacy_record, "expires_at": None, "expires_in": 3600},
            ),
            (
                "expiry_missing_duration",
                {
                    **legacy_record,
                    "expires_at": "2025-01-01T01:00:00+00:00",
                    "expires_in": None,
                },
            ),
            (
                "invalid_expires_at",
                {**legacy_record, "expires_at": "not-a-timestamp"},
            ),
            (
                "invalid_created_at",
                {**legacy_record, "created_at": "not-a-timestamp"},
            ),
            (
                "invalid_updated_at",
                {**legacy_record, "updated_at": "not-a-timestamp"},
            ),
            ("noncanonical_email", {**legacy_record, "email": "Verified@gmail.com"}),
        )
        for label, record in ambiguous_records:
            with self.subTest(label=label):
                classification = (
                    oauth_callback._classify_existing_google_token_record(
                        record,
                        normalized_email="verified@gmail.com",
                        normalized_owner_email="owner@example.com",
                    )
                )
            self.assertEqual(
                classification,
                oauth_callback.GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS,
            )

        invalid_equal_email = "not-a-mailbox"
        invalid_identity_records = (
            {
                **current_record,
                "email": invalid_equal_email,
                "owner_email": "",
            },
            {
                **{
                    key: value
                    for key, value in current_record.items()
                    if key != "provider"
                },
                "email": invalid_equal_email,
            },
        )
        for record in invalid_identity_records:
            classification = oauth_callback._classify_existing_google_token_record(
                record,
                normalized_email=invalid_equal_email,
                normalized_owner_email="owner@example.com",
            )
            self.assertEqual(
                classification,
                oauth_callback.GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS,
            )
            self.assertEqual(
                oauth_callback._resolve_google_token_conflict_diagnostic_code(
                    record,
                    record_classification=classification,
                    normalized_email=invalid_equal_email,
                ),
                "token_record_malformed",
            )

        self.assertEqual(
            oauth_callback._resolve_google_token_conflict_diagnostic_code(
                current_record,
                record_classification="CANARY_UNKNOWN_CLASSIFICATION",
                normalized_email="verified@gmail.com",
            ),
            "token_owner_conflict",
        )

    def test_legacy_mailbox_owner_classification_requires_exact_provenance_shape(self):
        current_record = durable_google_token(
            owner_email="verified@gmail.com",
        )
        expected_match = (
            oauth_callback.GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH
        )
        rejected_matches = {
            oauth_callback.GOOGLE_TOKEN_RECORD_OWNER_MISMATCH,
            oauth_callback.GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH,
            oauth_callback.GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS,
        }
        current_fields = set(oauth_callback.CURRENT_GOOGLE_TOKEN_RECORD_FIELDS)
        self.assertEqual(set(current_record), current_fields)

        for historical_owner in (
            "verified@gmail.com",
            " Verified@Gmail.com ",
        ):
            with self.subTest(historical_owner=historical_owner):
                self.assertEqual(
                    oauth_callback._classify_existing_google_token_record(
                        {**current_record, "owner_email": historical_owner},
                        normalized_email="verified@gmail.com",
                        normalized_owner_email="owner@example.com",
                    ),
                    expected_match,
                )

        self.assertEqual(
            oauth_callback._classify_existing_google_token_record(
                current_record,
                normalized_email="verified@gmail.com",
                normalized_owner_email="verified@gmail.com",
            ),
            oauth_callback.GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH,
        )

        near_matches = (
            (
                "substring_prefix",
                {**current_record, "owner_email": "xverified@gmail.com"},
            ),
            (
                "substring_suffix",
                {**current_record, "owner_email": "verified@gmail.com.invalid"},
            ),
            (
                "alias_plus_address",
                {**current_record, "owner_email": "verified+alias@gmail.com"},
            ),
            ("empty_owner", {**current_record, "owner_email": ""}),
            ("wrong_provider", {**current_record, "provider": "microsoft"}),
            ("wrong_email", {**current_record, "email": "other@gmail.com"}),
            ("malformed_access", {**current_record, "access_token": []}),
            (
                "modern_owner_alias",
                {**current_record, "ownerEmail": "verified@gmail.com"},
            ),
            (
                "modern_owner_ids",
                {
                    **current_record,
                    "userId": "CANARY_USER_ID",
                    "workspaceId": "CANARY_WORKSPACE_ID",
                },
            ),
        )
        for label, record in near_matches:
            with self.subTest(label=label):
                classification = (
                    oauth_callback._classify_existing_google_token_record(
                        record,
                        normalized_email="verified@gmail.com",
                        normalized_owner_email="owner@example.com",
                    )
                )
            self.assertIn(classification, rejected_matches)

        for missing_field in sorted(current_fields - {"owner_email"}):
            partial_record = {
                key: value
                for key, value in current_record.items()
                if key != missing_field
            }
            with self.subTest(missing_field=missing_field):
                classification = (
                    oauth_callback._classify_existing_google_token_record(
                        partial_record,
                        normalized_email="verified@gmail.com",
                        normalized_owner_email="owner@example.com",
                    )
                )
            self.assertIn(classification, rejected_matches)

        ownerless_record = {
            key: value
            for key, value in current_record.items()
            if key != "owner_email"
        }
        self.assertEqual(
            oauth_callback._classify_existing_google_token_record(
                ownerless_record,
                normalized_email="verified@gmail.com",
                normalized_owner_email="owner@example.com",
            ),
            oauth_callback.GOOGLE_TOKEN_RECORD_LEGACY_OWNERLESS_MATCH,
        )

    def test_absent_and_exact_owner_google_tokens_keep_existing_paths(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }

        def successful_write(_config, _store_key, _expected_record, record):
            return dict(record), None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=durable_config,
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            return_value=(None, None),
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
            side_effect=successful_write,
        ) as absent_write:
            absent_persisted, absent_error = (
                oauth_callback.persist_google_token_record(
                    email="VERIFIED@gmail.com",
                    owner_email="OWNER@example.com",
                    token_payload={"access_token": "first-access-token"},
                )
            )

        self.assertIsNone(absent_error)
        absent_write.assert_called_once()
        self.assertEqual(absent_persisted["access_token"], "first-access-token")
        self.assertIsNone(absent_persisted["refresh_token"])
        self.assertEqual(absent_persisted["owner_email"], "owner@example.com")

        valid_existing = {
            "provider": "google",
            "email": "verified@gmail.com",
            "owner_email": "owner@example.com",
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=durable_config,
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            return_value=(valid_existing, None),
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
            side_effect=successful_write,
        ) as owner_write:
            owner_persisted, owner_error = (
                oauth_callback.persist_google_token_record(
                    email="VERIFIED@gmail.com",
                    owner_email="OWNER@example.com",
                    token_payload={"access_token": "new-access-token"},
                )
            )

        self.assertIsNone(owner_error)
        owner_write.assert_called_once()
        self.assertEqual(owner_persisted["provider"], "google")
        self.assertEqual(owner_persisted["email"], "verified@gmail.com")
        self.assertEqual(owner_persisted["owner_email"], "owner@example.com")
        self.assertEqual(owner_persisted["access_token"], "new-access-token")
        self.assertEqual(owner_persisted["refresh_token"], "old-refresh-token")
        self.assertTrue(owner_persisted["_storage_durable"])

    def test_atomic_token_create_and_same_owner_replace_have_one_winner(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        store_key = oauth_callback._build_store_key("verified@gmail.com")

        def run_interleaving(expected_record, first_record, second_record):
            stored_value = (
                json.dumps(
                    expected_record,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if expected_record is not None
                else None
            )
            nested_result = None
            entered_first_mutation = False
            scripts = []

            def transport(_config, method, path, body=None):
                nonlocal stored_value, nested_result, entered_first_mutation
                if method == "GET":
                    return {"result": stored_value}, None

                self.assertEqual((method, path), ("POST", ""))
                command = json.loads(body)
                scripts.append(command[1])
                if not entered_first_mutation:
                    entered_first_mutation = True
                    nested_result = oauth_callback._write_durable_record(
                        durable_config,
                        store_key,
                        expected_record,
                        second_record,
                    )

                if (
                    command[1]
                    == oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT
                ):
                    if stored_value is not None:
                        return {"result": 0}, None
                    stored_value = command[4]
                    return {"result": 1}, None

                self.assertEqual(
                    command[1],
                    oauth_callback.GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
                )
                if stored_value != command[4]:
                    return {"result": 0}, None
                stored_value = command[5]
                return {"result": 1}, None

            with patch.object(
                oauth_callback,
                "_perform_rest_request",
                side_effect=transport,
            ):
                first_result = oauth_callback._write_durable_record(
                    durable_config,
                    store_key,
                    expected_record,
                    first_record,
                )

            return first_result, nested_result, json.loads(stored_value), scripts

        missing_first = durable_google_token(
            owner_email="first-owner@example.com",
            access_token="first-create-access",
            refresh_token="first-create-refresh",
        )
        missing_second = durable_google_token(
            owner_email="second-owner@example.com",
            access_token="second-create-access",
            refresh_token="second-create-refresh",
        )
        first_result, second_result, stored, scripts = run_interleaving(
            None,
            missing_first,
            missing_second,
        )
        self.assertIsNone(first_result[0])
        self.assertEqual(first_result[1]["code"], "token_owner_conflict")
        self.assertEqual(second_result, (missing_second, None))
        self.assertEqual(stored, missing_second)
        self.assertEqual(
            scripts,
            [
                oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
                oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
            ],
        )

        old_record = durable_google_token(
            access_token="old-access",
            refresh_token="old-refresh",
        )
        replacement_first = {
            **old_record,
            "access_token": "first-replacement-access",
        }
        replacement_second = {
            **old_record,
            "access_token": "second-replacement-access",
        }
        first_result, second_result, stored, scripts = run_interleaving(
            old_record,
            replacement_first,
            replacement_second,
        )
        self.assertIsNone(first_result[0])
        self.assertEqual(first_result[1]["code"], "token_owner_conflict")
        self.assertEqual(second_result, (replacement_second, None))
        self.assertEqual(stored, replacement_second)
        self.assertEqual(
            scripts,
            [
                oauth_callback.GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
                oauth_callback.GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
            ],
        )

    def test_stale_missing_read_cannot_overwrite_other_owner_create(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        stored_value = None
        stale_next_read = False
        successful_mutations = 0

        def transport(_config, method, path, body=None):
            nonlocal stored_value, stale_next_read, successful_mutations
            if method == "GET":
                if stale_next_read:
                    stale_next_read = False
                    return {"result": None}, None
                return {"result": stored_value}, None

            self.assertEqual((method, path), ("POST", ""))
            command = json.loads(body)
            self.assertEqual(
                command[1],
                oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
            )
            if stored_value is not None:
                return {"result": 0}, None
            successful_mutations += 1
            stored_value = command[4]
            return {"result": 1}, None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=durable_config,
        ), patch.object(
            oauth_callback,
            "_perform_rest_request",
            side_effect=transport,
        ):
            first_persisted, first_error = (
                oauth_callback.persist_google_token_record(
                    email="verified@gmail.com",
                    owner_email="first-owner@example.com",
                    token_payload={
                        "access_token": "first-owner-access",
                        "refresh_token": "first-owner-refresh",
                    },
                )
            )
            stale_next_read = True
            second_persisted, second_error = (
                oauth_callback.persist_google_token_record(
                    email="verified@gmail.com",
                    owner_email="second-owner@example.com",
                    token_payload={
                        "access_token": "second-owner-access",
                        "refresh_token": "second-owner-refresh",
                    },
                )
            )

        self.assertIsNone(first_error)
        self.assertEqual(first_persisted["owner_email"], "first-owner@example.com")
        self.assertIsNone(second_persisted)
        self.assertEqual(second_error["code"], "token_owner_conflict")
        self.assertEqual(successful_mutations, 1)
        self.assertEqual(
            json.loads(stored_value)["owner_email"],
            "first-owner@example.com",
        )

    def test_lost_or_malformed_create_ack_uses_exact_readback(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        next_record = durable_google_token(
            access_token="fresh-create-access",
            refresh_token="fresh-create-refresh",
        )
        other_record = durable_google_token(
            owner_email="other-owner@example.com",
            access_token="other-owner-access",
            refresh_token="other-owner-refresh",
        )
        same_owner_other_attempt = durable_google_token(
            access_token="same-owner-other-attempt-access",
            refresh_token="same-owner-other-attempt-refresh",
        )
        cases = (
            ("own_record_present", "lost", next_record, None),
            ("malformed_ack_own_record", "malformed", next_record, None),
            (
                "record_still_missing",
                "lost",
                None,
                "token_persistence_failed",
            ),
            (
                "other_owner_won",
                "lost",
                other_record,
                "token_owner_conflict",
            ),
            (
                "same_owner_other_attempt_won",
                "lost",
                same_owner_other_attempt,
                "token_owner_conflict",
            ),
            (
                "readback_unavailable",
                "lost",
                next_record,
                "token_persistence_failed",
            ),
        )

        for label, ack_mode, committed_record, expected_error in cases:
            stored_value = None
            read_count = 0

            def transport(_config, method, path, body=None):
                nonlocal stored_value, read_count
                if method == "GET":
                    read_count += 1
                    if label == "readback_unavailable" and read_count == 2:
                        return None, {
                            "code": "token_persistence_failed",
                            "message": "private readback outage",
                        }
                    return {"result": stored_value}, None

                self.assertEqual((method, path), ("POST", ""))
                command = json.loads(body)
                self.assertEqual(
                    command[1],
                    oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
                )
                stored_value = (
                    json.dumps(
                        committed_record,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    if committed_record is not None
                    else None
                )
                if ack_mode == "malformed":
                    return {"result": "OK"}, None
                return None, {
                    "code": "token_persistence_failed",
                    "message": "private lost acknowledgement",
                }

            with self.subTest(case=label), patch.object(
                oauth_callback,
                "_perform_rest_request",
                side_effect=transport,
            ):
                persisted, error = oauth_callback._write_durable_record(
                    durable_config,
                    store_key,
                    None,
                    next_record,
                )

            if expected_error is None:
                self.assertEqual(persisted, next_record)
                self.assertIsNone(error)
            else:
                self.assertIsNone(persisted)
                self.assertEqual(error["code"], expected_error)
                self.assertNotIn("private", json.dumps(error))
                if label == "readback_unavailable":
                    self.assertEqual(
                        error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
                        "mailbox_readback_verification_failed",
                    )

    def test_ambiguous_or_mismatched_google_tokens_never_reach_write(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        current_record = durable_google_token(
            access_token="CANARY_OLD_ACCESS_TOKEN",
            refresh_token="CANARY_OLD_REFRESH_TOKEN",
        )
        legacy_record = {
            key: value
            for key, value in current_record.items()
            if key != "owner_email"
        }
        partial_owner_record = {
            key: value
            for key, value in current_record.items()
            if key != "provider"
        }
        partial_owner_without_email = {
            key: value
            for key, value in current_record.items()
            if key != "email"
        }
        legacy_mailbox_owner_near_match = {
            "provider": "google",
            "email": "verified@gmail.com",
            "owner_email": "verified@gmail.com",
            "access_token": "CANARY_OLD_ACCESS_TOKEN",
            "refresh_token": "CANARY_OLD_REFRESH_TOKEN",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        invalid_existing_records = (
            (
                "wrong_owner",
                {**current_record, "owner_email": "other@example.com"},
                "token_owner_mismatch",
            ),
            (
                "legacy_owner_equals_mailbox_without_exact_provenance_shape",
                legacy_mailbox_owner_near_match,
                "token_legacy_owner_equals_mailbox",
            ),
            (
                "owner_null",
                {**current_record, "owner_email": None},
                "token_record_malformed",
            ),
            (
                "owner_empty",
                {**current_record, "owner_email": ""},
                "token_owner_fields_empty",
            ),
            (
                "owner_whitespace",
                {**current_record, "owner_email": "   "},
                "token_owner_fields_empty",
            ),
            (
                "owner_wrong_type",
                {**current_record, "owner_email": []},
                "token_record_malformed",
            ),
            (
                "partial_owner_identity",
                partial_owner_record,
                "token_owner_fields_partial",
            ),
            (
                "partial_owner_identity_without_email",
                partial_owner_without_email,
                "token_owner_fields_partial",
            ),
            (
                "partial_owner_identity_without_access",
                {
                    key: value
                    for key, value in partial_owner_record.items()
                    if key != "access_token"
                },
                "token_record_malformed",
            ),
            (
                "partial_owner_identity_with_invalid_refresh",
                {**partial_owner_record, "refresh_token": []},
                "token_record_malformed",
            ),
            (
                "partial_owner_identity_with_unknown_field",
                {**partial_owner_record, "ownerEmail": "owner@example.com"},
                "token_record_malformed",
            ),
            (
                "unknown_owner_alias",
                {**legacy_record, "ownerEmail": "owner@example.com"},
                "token_record_malformed",
            ),
            (
                "matching_owner_with_conflicting_owner_identity",
                {**current_record, "ownerEmail": "other@example.com"},
                "token_record_malformed",
            ),
            (
                "matching_owner_with_malformed_refresh",
                {**current_record, "refresh_token": []},
                "token_record_malformed",
            ),
            (
                "empty_owner_with_noncanonical_email",
                {
                    **current_record,
                    "email": "Verified@gmail.com",
                    "owner_email": "",
                },
                "token_record_malformed",
            ),
            (
                "wrong_provider",
                {**legacy_record, "provider": "microsoft"},
                "token_provider_mismatch",
            ),
            (
                "wrong_email",
                {**legacy_record, "email": "other@gmail.com"},
                "token_email_mismatch",
            ),
            (
                "malformed_record",
                {**legacy_record, "access_token": None},
                "token_record_malformed",
            ),
            ("non_dict_record", [legacy_record], "token_record_malformed"),
        )

        for label, existing_record, expected_diagnostic in invalid_existing_records:
            snapshot = json.dumps(existing_record, sort_keys=True)
            with self.subTest(label=label), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value=durable_config,
            ), patch.object(
                oauth_callback,
                "_read_durable_record",
                return_value=(existing_record, None),
            ), patch.object(
                oauth_callback,
                "_write_durable_record",
            ) as durable_write, patch.object(
                oauth_callback,
                "_adopt_legacy_durable_record",
            ) as legacy_write:
                persisted, error = oauth_callback.persist_google_token_record(
                    email="VERIFIED@gmail.com",
                    owner_email="OWNER@example.com",
                    token_payload={
                        "access_token": "CANARY_NEW_ACCESS_TOKEN",
                        "refresh_token": "CANARY_NEW_REFRESH_TOKEN",
                    },
                )

            self.assertIsNone(persisted)
            self.assertEqual(error["code"], "token_owner_conflict")
            self.assertEqual(
                error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
                expected_diagnostic,
            )
            durable_write.assert_not_called()
            legacy_write.assert_not_called()
            self.assertEqual(json.dumps(existing_record, sort_keys=True), snapshot)

    def test_rejected_google_token_diagnostics_are_log_only_and_never_write(self):
        from urllib.parse import urlencode

        verified_email = "verified@gmail.com"
        current_owner = "current-owner-canary@example.invalid"
        wrong_owner = "wrong-owner-canary@example.invalid"
        stored_email = "stored-mailbox-canary@example.invalid"
        stored_provider = "CANARY_STORED_PROVIDER"
        malformed_owner = "CANARY_MALFORMED_OWNER_VALUE"
        old_access = "CANARY_DIAGNOSTIC_OLD_ACCESS_TOKEN"
        old_refresh = "CANARY_DIAGNOSTIC_OLD_REFRESH_TOKEN"
        fresh_access = "CANARY_DIAGNOSTIC_FRESH_ACCESS_TOKEN"
        fresh_refresh = "CANARY_DIAGNOSTIC_FRESH_REFRESH_TOKEN"
        state_secret = "CANARY_DIAGNOSTIC_STATE_SECRET"
        client_secret = "CANARY_DIAGNOSTIC_CLIENT_SECRET"
        request_cookie = "CANARY_DIAGNOSTIC_REQUEST_COOKIE"
        user_id = "CANARY_DIAGNOSTIC_USER_ID"
        workspace_id = "CANARY_DIAGNOSTIC_WORKSPACE_ID"
        mailbox_id = "CANARY_DIAGNOSTIC_MAILBOX_ID"
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "CANARY_DIAGNOSTIC_KV_TOKEN",
        }
        current_record = durable_google_token(
            owner_email=current_owner,
            access_token=old_access,
            refresh_token=old_refresh,
        )
        legacy_record = {
            key: value
            for key, value in current_record.items()
            if key != "owner_email"
        }
        partial_owner_record = {
            key: value
            for key, value in current_record.items()
            if key != "provider"
        }
        legacy_mailbox_owner_near_match = {
            "provider": "google",
            "email": verified_email,
            "owner_email": verified_email,
            "access_token": old_access,
            "refresh_token": old_refresh,
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        cases = (
            (
                "wrong_owner",
                {**current_record, "owner_email": wrong_owner},
                "token_owner_mismatch",
                True,
            ),
            (
                "legacy_owner_equals_mailbox_without_exact_provenance_shape",
                legacy_mailbox_owner_near_match,
                "token_legacy_owner_equals_mailbox",
                True,
            ),
            (
                "legacy_owner_fuzzy_match",
                {**current_record, "owner_email": "verified@gmail.com.invalid"},
                "token_owner_mismatch",
                True,
            ),
            (
                "legacy_owner_substring_prefix",
                {**current_record, "owner_email": "xverified@gmail.com"},
                "token_owner_mismatch",
                True,
            ),
            (
                "legacy_owner_plus_alias",
                {**current_record, "owner_email": "verified+alias@gmail.com"},
                "token_owner_mismatch",
                True,
            ),
            (
                "legacy_owner_with_modern_owner_alias",
                {
                    **current_record,
                    "owner_email": verified_email,
                    "ownerEmail": verified_email,
                },
                "token_record_malformed",
                True,
            ),
            (
                "legacy_owner_with_modern_identity_fields",
                {
                    **current_record,
                    "owner_email": verified_email,
                    "userId": user_id,
                    "workspaceId": workspace_id,
                },
                "token_record_malformed",
                True,
            ),
            (
                "empty_owner",
                {**current_record, "owner_email": "   "},
                "token_owner_fields_empty",
                True,
            ),
            (
                "partial_owner_identity",
                partial_owner_record,
                "token_owner_fields_partial",
                True,
            ),
            (
                "provider_mismatch",
                {**legacy_record, "provider": stored_provider},
                "token_provider_mismatch",
                True,
            ),
            (
                "email_mismatch",
                {**legacy_record, "email": stored_email},
                "token_email_mismatch",
                True,
            ),
            (
                "malformed_owner",
                {**current_record, "owner_email": [malformed_owner]},
                "token_record_malformed",
                True,
            ),
            (
                "ownerless_durable",
                legacy_record,
                "token_owner_conflict",
                True,
            ),
            (
                "safe_runtime_fallback",
                legacy_record,
                "token_owner_conflict",
                False,
            ),
        )
        expected_payload = {
            "status": "error",
            "provider": "google",
            "email": verified_email,
            "message": (
                "Google authentication completed, but secure authorization "
                "storage is unavailable."
            ),
        }
        expected_html = None
        member = auth_runtime.AuthenticatedMemberContext(
            user_id=user_id,
            email=current_owner,
            name="Diagnostic Owner",
            workspace_id=workspace_id,
            membership_role="owner",
        )

        class CapturingCallback(FakeHandler):
            def __init__(self, path):
                super().__init__(
                    raw_body=b"",
                    headers={"cookie": request_cookie},
                )
                self.path = path
                self.callback_payload = None

            def _send_callback_page(self, payload, *, set_cookies=()):
                self.callback_payload = payload
                oauth_callback.handler._send_callback_page(
                    self,
                    payload,
                    set_cookies=set_cookies,
                )

        for label, existing_record, expected_code, use_durable_store in cases:
            authorization_code = f"CANARY_DIAGNOSTIC_AUTH_CODE_{label}"
            state, verifier = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                current_owner,
                state_secret,
                "main",
                member_user_id=user_id,
                member_workspace_id=workspace_id,
            )
            request_path = (
                "/api/inboxes/oauth-callback?"
                + urlencode({"code": authorization_code, "state": state})
            )
            callback = CapturingCallback(request_path)
            logger = Mock()
            store_key = oauth_callback._build_store_key(verified_email)
            record_snapshot = json.dumps(existing_record, sort_keys=True)

            with self.subTest(label=label), patch.dict(
                oauth_callback.os.environ,
                self._production_oauth_environment(
                    CUEVION_OAUTH_STATE_SECRET=state_secret,
                    GOOGLE_CLIENT_SECRET=client_secret,
                ),
                clear=True,
            ), patch.object(
                oauth_callback,
                "_GMAIL_CALLBACK_LOGGER",
                logger,
            ), patch.object(
                oauth_callback,
                "_resolve_authenticated_member_request",
                return_value=(member, ()),
            ), patch.object(
                oauth_callback,
                "_exchange_google_code",
                return_value=(
                    {
                        "access_token": fresh_access,
                        "refresh_token": fresh_refresh,
                    },
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_fetch_verified_google_identity",
                return_value=(
                    {"email": verified_email, "display_name": "Verified"},
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_prepare_gmail_managed_inbox_registration",
                return_value=({"prepared": True, "mailboxId": mailbox_id}, None),
            ), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value=(durable_config if use_durable_store else None),
            ), patch.object(
                oauth_callback,
                "_read_durable_record",
                return_value=(existing_record, None),
            ) as durable_read, patch.object(
                oauth_callback,
                "_read_runtime_store",
                return_value={store_key: existing_record},
            ) as runtime_read, patch.object(
                oauth_callback,
                "_write_durable_record",
            ) as durable_write, patch.object(
                oauth_callback,
                "_adopt_legacy_durable_record",
            ) as legacy_write, patch.object(
                oauth_callback,
                "_persist_runtime_record",
            ) as runtime_write, patch.object(
                oauth_callback,
                "_register_gmail_managed_inbox_in_user_config",
            ) as config_write, patch.object(
                oauth_callback,
                "urlopen",
            ) as outbound_request:
                oauth_callback.handler.do_GET(callback)

            if use_durable_store:
                durable_read.assert_called_once()
                runtime_read.assert_not_called()
            else:
                durable_read.assert_not_called()
                runtime_read.assert_called_once()
            durable_write.assert_not_called()
            legacy_write.assert_not_called()
            runtime_write.assert_not_called()
            config_write.assert_not_called()
            outbound_request.assert_not_called()
            self.assertEqual(
                json.dumps(existing_record, sort_keys=True),
                record_snapshot,
            )
            logger.warning.assert_called_once_with(
                "event=gmail_oauth_callback_failure "
                f"failure_code={expected_code} provider=google "
                "inbox_position=main"
            )
            self.assertIn(expected_code, oauth_callback.GMAIL_CALLBACK_FAILURE_CODES)
            self.assertEqual(callback.status, 200)
            self.assertEqual(callback.callback_payload, expected_payload)

            response_body = callback.wfile.getvalue().decode("utf-8")
            if expected_html is None:
                expected_html = response_body
            self.assertEqual(response_body, expected_html)
            response_artifacts = "\n".join(
                (
                    json.dumps(callback.callback_payload, sort_keys=True),
                    json.dumps(callback.response_headers, sort_keys=True),
                    response_body,
                )
            )
            for failure_code in oauth_callback.GMAIL_CALLBACK_FAILURE_CODES:
                self.assertNotIn(failure_code, response_artifacts)
            for forbidden_field in (
                "owner_email",
                "ownerEmail",
                "owner_binding",
                "access_token",
                "refresh_token",
                "userId",
                "workspaceId",
                "mailboxId",
            ):
                self.assertNotIn(forbidden_field, response_artifacts)

            combined_artifacts = "\n".join(
                (
                    logger.warning.call_args.args[0],
                    response_artifacts,
                )
            )
            for canary in (
                wrong_owner,
                stored_email,
                stored_provider,
                malformed_owner,
                old_access,
                old_refresh,
                fresh_access,
                fresh_refresh,
                current_owner,
                authorization_code,
                state,
                verifier,
                store_key,
                state_secret,
                client_secret,
                request_cookie,
                user_id,
                workspace_id,
                mailbox_id,
                durable_config["rest_token"],
            ):
                self.assertNotIn(canary, combined_artifacts)

            self.assertNotIn(verified_email, logger.warning.call_args.args[0])

    def test_legacy_mailbox_owner_adoption_uses_only_fresh_credentials_and_is_one_shot(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        old_values = {
            "access_token": "CANARY_LEGACY_ACCESS_TOKEN",
            "refresh_token": "CANARY_LEGACY_REFRESH_TOKEN",
            "token_type": "CANARY_LEGACY_TOKEN_TYPE",
            "scope": "CANARY_LEGACY_SCOPE",
            "expires_at": "2001-01-01T00:00:00+00:00",
            "expires_in": 111,
            "updated_at": "2000-01-01T00:00:00+00:00",
            "created_at": "2000-01-01T00:00:00+00:00",
        }
        legacy_record = durable_google_token(**old_values)
        legacy_record["owner_email"] = "verified@gmail.com"
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        legacy_raw_value = json.dumps(legacy_record, indent=2, sort_keys=False)
        stored_values = {store_key: legacy_raw_value}
        requests = []
        writes = []

        encoded_store_key = oauth_callback.quote(store_key, safe="")
        get_path = f"/get/{encoded_store_key}"

        def durable_transport(_config, method, path, body=None):
            requests.append((method, path))
            if method == "GET":
                self.assertEqual(path, get_path)
                return {"result": stored_values.get(store_key)}, None
            self.assertEqual(method, "POST")
            if path == "":
                command = json.loads(body)
                self.assertEqual(command[0], "EVAL")
                self.assertIn(
                    command[1],
                    {
                        oauth_callback.LEGACY_GOOGLE_TOKEN_ADOPTION_SCRIPT,
                        oauth_callback.GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
                    },
                )
                self.assertEqual(command[2:4], [1, store_key])
                self.assertEqual(command[6], oauth_callback.GMAIL_OAUTH_TOKEN_TTL_SECONDS)
                current_value = stored_values.get(store_key)
                if current_value != command[4]:
                    return {"result": 0}, None
                record = json.loads(command[5])
                writes.append(record)
                stored_values[store_key] = command[5]
                return {"result": 1}, None
            self.fail(f"Unexpected durable token request path: {path}")

        first_payload = {
            "access_token": "CANARY_FRESH_ACCESS_TOKEN_ONE",
            "refresh_token": "CANARY_FRESH_REFRESH_TOKEN_ONE",
            "token_type": "FreshBearer",
            "scope": "fresh-scope",
            "expires_in": 7200,
        }
        second_payload = {"access_token": "CANARY_FRESH_ACCESS_TOKEN_TWO"}
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=durable_config,
        ), patch.object(
            oauth_callback,
            "_perform_rest_request",
            side_effect=durable_transport,
        ), patch.object(oauth_callback, "urlopen") as external_call:
            first_persisted, first_error = (
                oauth_callback.persist_google_token_record(
                    email="VERIFIED@gmail.com",
                    owner_email="OWNER@example.com",
                    token_payload=first_payload,
                )
            )
            second_persisted, second_error = (
                oauth_callback.persist_google_token_record(
                    email="VERIFIED@gmail.com",
                    owner_email="OWNER@example.com",
                    token_payload=second_payload,
                )
            )

        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        external_call.assert_not_called()
        self.assertEqual(
            requests,
            [
                ("GET", get_path),
                ("GET", get_path),
                ("POST", ""),
                ("GET", get_path),
                ("GET", get_path),
                ("GET", get_path),
                ("POST", ""),
                ("GET", get_path),
            ],
        )
        self.assertEqual(len(stored_values), 1)
        self.assertEqual(len(writes), 2)

        adopted = writes[0]
        self.assertEqual(adopted["provider"], "google")
        self.assertEqual(adopted["email"], "verified@gmail.com")
        self.assertEqual(adopted["owner_email"], "owner@example.com")
        self.assertEqual(adopted["access_token"], first_payload["access_token"])
        self.assertEqual(adopted["refresh_token"], first_payload["refresh_token"])
        self.assertEqual(adopted["token_type"], first_payload["token_type"])
        self.assertEqual(adopted["scope"], first_payload["scope"])
        self.assertEqual(adopted["expires_in"], first_payload["expires_in"])
        self.assertNotEqual(adopted["expires_at"], old_values["expires_at"])
        self.assertNotEqual(adopted["created_at"], old_values["created_at"])
        self.assertNotEqual(adopted["updated_at"], old_values["updated_at"])
        self.assertEqual(
            {key for key in adopted if key != "owner_email"},
            set(oauth_callback.LEGACY_GOOGLE_TOKEN_RECORD_FIELDS),
        )
        serialized_adopted = json.dumps(adopted, sort_keys=True)
        for old_canary in (
            old_values["access_token"],
            old_values["refresh_token"],
            old_values["token_type"],
            old_values["scope"],
            old_values["expires_at"],
            old_values["created_at"],
            old_values["updated_at"],
        ):
            self.assertNotIn(str(old_canary), serialized_adopted)

        self.assertEqual(first_persisted["owner_email"], "owner@example.com")
        self.assertTrue(first_persisted["_storage_durable"])
        self.assertEqual(writes[1]["access_token"], second_payload["access_token"])
        self.assertEqual(writes[1]["refresh_token"], first_payload["refresh_token"])
        self.assertNotEqual(writes[1]["refresh_token"], old_values["refresh_token"])
        self.assertEqual(second_persisted["owner_email"], "owner@example.com")

    def test_legacy_adoption_atomically_refuses_a_concurrent_owner_change(self):
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token="CANARY_RACE_LEGACY_ACCESS",
            refresh_token="CANARY_RACE_LEGACY_REFRESH",
        )
        competing_record = durable_google_token(
            owner_email="other-owner@example.com",
            access_token="CANARY_RACE_COMPETING_ACCESS",
            refresh_token="CANARY_RACE_COMPETING_REFRESH",
        )
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        stored_value = json.dumps(legacy_record, indent=2, sort_keys=False)
        requests = []
        set_count = 0
        get_count = 0

        def durable_transport(_config, method, path, body=None):
            nonlocal stored_value, set_count, get_count
            requests.append((method, path))
            if method == "GET":
                get_count += 1
                result = stored_value
                if get_count == 2:
                    stored_value = json.dumps(
                        competing_record,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                return {"result": result}, None

            self.assertEqual((method, path), ("POST", ""))
            command = json.loads(body)
            self.assertEqual(command[0], "EVAL")
            self.assertEqual(command[3], store_key)
            if stored_value != command[4]:
                return {"result": 0}, None
            set_count += 1
            stored_value = command[5]
            return {"result": 1}, None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "_perform_rest_request",
            side_effect=durable_transport,
        ), patch.object(oauth_callback, "urlopen") as external_call:
            persisted, error = oauth_callback.persist_google_token_record(
                email="verified@gmail.com",
                owner_email="owner@example.com",
                token_payload={
                    "access_token": "CANARY_RACE_FRESH_ACCESS",
                    "refresh_token": "CANARY_RACE_FRESH_REFRESH",
                },
            )

        self.assertIsNone(persisted)
        self.assertEqual(error["code"], "token_owner_conflict")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "token_owner_conflict",
        )
        self.assertEqual(set_count, 0)
        self.assertEqual(json.loads(stored_value), competing_record)
        self.assertEqual(
            [method for method, _path in requests],
            ["GET", "GET", "POST"],
        )
        external_call.assert_not_called()

    def test_legacy_mailbox_owner_adoption_uses_fresh_credentials_and_exact_cas(self):
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        encoded_store_key = oauth_callback.quote(store_key, safe="")
        get_path = f"/get/{encoded_store_key}"
        old_values = {
            "access_token": "CANARY_MAILBOX_OWNER_OLD_ACCESS",
            "refresh_token": "CANARY_MAILBOX_OWNER_OLD_REFRESH",
            "token_type": "CANARY_MAILBOX_OWNER_OLD_TYPE",
            "scope": "CANARY_MAILBOX_OWNER_OLD_SCOPE",
            "expires_at": "2001-01-01T00:00:00+00:00",
            "expires_in": 111,
            "updated_at": "2000-01-01T00:00:00+00:00",
            "created_at": "2000-01-01T00:00:00+00:00",
        }
        first_payload = {
            "access_token": "CANARY_MAILBOX_OWNER_FRESH_ACCESS_ONE",
            "refresh_token": "CANARY_MAILBOX_OWNER_FRESH_REFRESH_ONE",
            "token_type": "FreshBearer",
            "scope": "fresh-scope",
            "expires_in": 7200,
        }
        second_payload = {
            "access_token": "CANARY_MAILBOX_OWNER_FRESH_ACCESS_TWO",
        }

        for historical_owner in (
            "verified@gmail.com",
            " Verified@Gmail.com ",
        ):
            legacy_record = durable_google_token(
                owner_email=historical_owner,
                **old_values,
            )
            legacy_raw_value = json.dumps(
                legacy_record,
                indent=2,
                sort_keys=False,
            )
            stored_values = {store_key: legacy_raw_value}
            requests = []
            writes = []

            def durable_transport(_config, method, path, body=None):
                requests.append((method, path))
                if method == "GET":
                    self.assertEqual(path, get_path)
                    return {"result": stored_values.get(store_key)}, None

                self.assertEqual(method, "POST")
                if path == "":
                    command = json.loads(body)
                    self.assertEqual(command[0], "EVAL")
                    self.assertIn(
                        command[1],
                        {
                            oauth_callback.LEGACY_GOOGLE_TOKEN_ADOPTION_SCRIPT,
                            oauth_callback.GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
                        },
                    )
                    self.assertEqual(command[2:4], [1, store_key])
                    self.assertEqual(
                        command[4],
                        stored_values.get(store_key),
                    )
                    self.assertEqual(
                        command[6],
                        oauth_callback.GMAIL_OAUTH_TOKEN_TTL_SECONDS,
                    )
                    if stored_values.get(store_key) != command[4]:
                        return {"result": 0}, None
                    record = json.loads(command[5])
                    writes.append(("cas", record))
                    stored_values[store_key] = command[5]
                    return {"result": 1}, None

                self.fail(f"Unexpected durable token request path: {path}")

            with self.subTest(historical_owner=historical_owner), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value=durable_config,
            ), patch.object(
                oauth_callback,
                "_perform_rest_request",
                side_effect=durable_transport,
            ), patch.object(
                oauth_callback,
                "urlopen",
            ) as external_call:
                first_persisted, first_error = (
                    oauth_callback.persist_google_token_record(
                        email="VERIFIED@gmail.com",
                        owner_email="OWNER@example.com",
                        token_payload=first_payload,
                    )
                )
                second_persisted, second_error = (
                    oauth_callback.persist_google_token_record(
                        email="VERIFIED@gmail.com",
                        owner_email="OWNER@example.com",
                        token_payload=second_payload,
                    )
                )

            self.assertIsNone(first_error)
            self.assertIsNone(second_error)
            external_call.assert_not_called()
            self.assertEqual(
                requests,
                [
                    ("GET", get_path),
                    ("GET", get_path),
                    ("POST", ""),
                    ("GET", get_path),
                    ("GET", get_path),
                    ("GET", get_path),
                    ("POST", ""),
                    ("GET", get_path),
                ],
            )
            self.assertEqual(len(stored_values), 1)
            self.assertEqual(
                [write_type for write_type, _ in writes],
                ["cas", "cas"],
            )

            adopted = writes[0][1]
            self.assertEqual(
                set(adopted),
                set(oauth_callback.CURRENT_GOOGLE_TOKEN_RECORD_FIELDS),
            )
            self.assertEqual(adopted["provider"], "google")
            self.assertEqual(adopted["email"], "verified@gmail.com")
            self.assertEqual(adopted["owner_email"], "owner@example.com")
            self.assertEqual(adopted["access_token"], first_payload["access_token"])
            self.assertEqual(adopted["refresh_token"], first_payload["refresh_token"])
            self.assertEqual(adopted["token_type"], first_payload["token_type"])
            self.assertEqual(adopted["scope"], first_payload["scope"])
            self.assertEqual(adopted["expires_in"], first_payload["expires_in"])
            self.assertNotEqual(adopted["expires_at"], old_values["expires_at"])
            self.assertNotEqual(adopted["created_at"], old_values["created_at"])
            self.assertNotEqual(adopted["updated_at"], old_values["updated_at"])
            serialized_adopted = json.dumps(adopted, sort_keys=True)
            for field in (
                "access_token",
                "refresh_token",
                "token_type",
                "scope",
                "expires_at",
                "updated_at",
                "created_at",
            ):
                self.assertNotIn(str(old_values[field]), serialized_adopted)

            self.assertEqual(
                oauth_callback._classify_existing_google_token_record(
                    adopted,
                    normalized_email="verified@gmail.com",
                    normalized_owner_email="owner@example.com",
                ),
                oauth_callback.GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH,
            )
            self.assertEqual(first_persisted["owner_email"], "owner@example.com")
            self.assertTrue(first_persisted["_storage_durable"])
            updated = writes[1][1]
            self.assertEqual(updated["access_token"], second_payload["access_token"])
            self.assertEqual(updated["refresh_token"], first_payload["refresh_token"])
            self.assertEqual(second_persisted["owner_email"], "owner@example.com")

    def test_legacy_mailbox_owner_adoption_refuses_concurrent_owner_change(self):
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token="CANARY_MAILBOX_RACE_OLD_ACCESS",
            refresh_token="CANARY_MAILBOX_RACE_OLD_REFRESH",
        )
        competing_record = durable_google_token(
            owner_email="other-owner@example.com",
            access_token="CANARY_MAILBOX_RACE_COMPETING_ACCESS",
            refresh_token="CANARY_MAILBOX_RACE_COMPETING_REFRESH",
        )
        store_key = oauth_callback._build_store_key("verified@gmail.com")
        legacy_raw_value = json.dumps(legacy_record, indent=2, sort_keys=False)
        stored_value = legacy_raw_value
        requests = []
        set_count = 0
        get_count = 0

        def durable_transport(_config, method, path, body=None):
            nonlocal stored_value, set_count, get_count
            requests.append((method, path))
            if method == "GET":
                get_count += 1
                result = stored_value
                if get_count == 2:
                    stored_value = json.dumps(
                        competing_record,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                return {"result": result}, None

            self.assertEqual((method, path), ("POST", ""))
            command = json.loads(body)
            self.assertEqual(command[0], "EVAL")
            self.assertEqual(command[3], store_key)
            self.assertEqual(command[4], legacy_raw_value)
            if stored_value != command[4]:
                return {"result": 0}, None
            set_count += 1
            stored_value = command[5]
            return {"result": 1}, None

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "_perform_rest_request",
            side_effect=durable_transport,
        ), patch.object(
            oauth_callback,
            "urlopen",
        ) as external_call:
            persisted, error = oauth_callback.persist_google_token_record(
                email="verified@gmail.com",
                owner_email="owner@example.com",
                token_payload={
                    "access_token": "CANARY_MAILBOX_RACE_FRESH_ACCESS",
                    "refresh_token": "CANARY_MAILBOX_RACE_FRESH_REFRESH",
                },
            )

        self.assertIsNone(persisted)
        self.assertEqual(error["code"], "token_owner_conflict")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "token_owner_conflict",
        )
        self.assertEqual(set_count, 0)
        self.assertEqual(json.loads(stored_value), competing_record)
        self.assertEqual(
            [method for method, _path in requests],
            ["GET", "GET", "POST"],
        )
        external_call.assert_not_called()

    def test_legacy_ownerless_record_is_never_adopted(self):
        legacy_record = durable_google_token()
        legacy_record.pop("owner_email")
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            return_value=(legacy_record, None),
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
        ) as durable_write, patch.object(
            oauth_callback,
            "_adopt_legacy_durable_record",
        ) as legacy_write:
            persisted, error = oauth_callback.persist_google_token_record(
                email="verified@gmail.com",
                owner_email="owner@example.com",
                token_payload={
                    "access_token": "CANARY_FRESH_ACCESS_ONLY",
                    "refresh_token": "CANARY_FRESH_REFRESH",
                },
            )

        self.assertIsNone(persisted)
        self.assertEqual(error["code"], "token_owner_conflict")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "token_owner_conflict",
        )
        durable_write.assert_not_called()
        legacy_write.assert_not_called()

        store_key = oauth_callback._build_store_key("verified@gmail.com")
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=None,
        ), patch.object(
            oauth_callback,
            "_read_runtime_store",
            return_value={store_key: legacy_record},
        ), patch.object(
            oauth_callback,
            "_persist_runtime_record",
        ) as runtime_write:
            runtime_persisted, runtime_error = (
                oauth_callback.persist_google_token_record(
                    email="verified@gmail.com",
                    owner_email="owner@example.com",
                    token_payload={
                        "access_token": "CANARY_RUNTIME_ACCESS",
                        "refresh_token": "CANARY_RUNTIME_REFRESH",
                    },
                )
            )

        self.assertIsNone(runtime_persisted)
        self.assertEqual(runtime_error["code"], "token_owner_conflict")
        runtime_write.assert_not_called()

    def test_legacy_mailbox_owner_adoption_requires_fresh_refresh_and_durable_store(self):
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token="CANARY_MAILBOX_OWNER_OLD_ACCESS",
            refresh_token="CANARY_MAILBOX_OWNER_OLD_REFRESH",
        )
        durable_config = {
            "backend": "vercel_kv_rest",
            "rest_url": "https://kv.example",
            "rest_token": "secret",
        }
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=durable_config,
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            return_value=(legacy_record, None),
        ), patch.object(
            oauth_callback,
            "_write_durable_record",
        ) as durable_write, patch.object(
            oauth_callback,
            "_adopt_legacy_durable_record",
        ) as legacy_write:
            persisted, error = oauth_callback.persist_google_token_record(
                email="verified@gmail.com",
                owner_email="owner@example.com",
                token_payload={"access_token": "CANARY_FRESH_ACCESS_ONLY"},
            )

        self.assertIsNone(persisted)
        self.assertEqual(error["code"], "invalid_token_payload")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "token_payload_invalid",
        )
        durable_write.assert_not_called()
        legacy_write.assert_not_called()

        store_key = oauth_callback._build_store_key("verified@gmail.com")
        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value=None,
        ), patch.object(
            oauth_callback,
            "_read_runtime_store",
            return_value={store_key: legacy_record},
        ), patch.object(
            oauth_callback,
            "_persist_runtime_record",
        ) as runtime_write:
            runtime_persisted, runtime_error = (
                oauth_callback.persist_google_token_record(
                    email="verified@gmail.com",
                    owner_email="owner@example.com",
                    token_payload={
                        "access_token": "CANARY_RUNTIME_ACCESS",
                        "refresh_token": "CANARY_RUNTIME_REFRESH",
                    },
                )
            )

        self.assertIsNone(runtime_persisted)
        self.assertEqual(runtime_error["code"], "token_owner_conflict")
        self.assertEqual(
            runtime_error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "token_legacy_owner_equals_mailbox",
        )
        runtime_write.assert_not_called()

    def test_callback_adopts_legacy_token_before_mailbox_registration(self):
        owner_email = "owner@example.com"
        fresh_access = "CANARY_CALLBACK_FRESH_ACCESS"
        fresh_refresh = "CANARY_CALLBACK_FRESH_REFRESH"
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token="CANARY_CALLBACK_LEGACY_ACCESS",
            refresh_token="CANARY_CALLBACK_LEGACY_REFRESH",
        )
        stored_record = json.loads(json.dumps(legacy_record))
        events = []
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            owner_email,
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = self._google_callback(state)
        logger = Mock()

        def preflight(*_args, **_kwargs):
            events.append("preflight")
            return {"prepared": True}, None

        def write_token(_config, _store_key, existing_record, record):
            nonlocal stored_record
            events.append("token_write_and_readback")
            self.assertEqual(existing_record, legacy_record)
            stored_record = json.loads(json.dumps(record))
            return json.loads(json.dumps(stored_record)), None

        def register_mailbox(*_args, **_kwargs):
            events.append("mailbox_registration")
            self.assertEqual(stored_record["provider"], "google")
            self.assertEqual(stored_record["email"], "verified@gmail.com")
            self.assertEqual(stored_record["owner_email"], owner_email)
            self.assertEqual(stored_record["access_token"], fresh_access)
            self.assertEqual(stored_record["refresh_token"], fresh_refresh)
            return {"id": "gmail-verified"}, None

        with patch.dict(
            oauth_callback.os.environ,
            self._production_oauth_environment(),
            clear=True,
        ), patch.object(
            oauth_callback,
            "_GMAIL_CALLBACK_LOGGER",
            logger,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(owner_email), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=(
                {
                    "access_token": fresh_access,
                    "refresh_token": fresh_refresh,
                    "expires_in": 3600,
                },
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": "verified@gmail.com", "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
            side_effect=preflight,
        ), patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            return_value=(legacy_record, None),
        ), patch.object(
            oauth_callback,
            "_adopt_legacy_durable_record",
            side_effect=write_token,
        ), patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
            side_effect=register_mailbox,
        ) as register:
            oauth_callback.handler.do_GET(callback)

        self.assertEqual(
            events,
            ["preflight", "token_write_and_readback", "mailbox_registration"],
        )
        register.assert_called_once()
        logger.warning.assert_not_called()
        payload = callback._send_callback_page.call_args.args[0]
        self.assertEqual(
            payload,
            {
                "status": "success",
                "provider": "google",
                "inboxPosition": "main",
                "email": "verified@gmail.com",
                "mailboxId": "gmail-verified",
                "message": (
                    "Google account connected. Durable mailbox token storage is active."
                ),
            },
        )
        serialized_payload = json.dumps(payload, sort_keys=True)
        for canary in (
            fresh_access,
            fresh_refresh,
            legacy_record["access_token"],
            legacy_record["refresh_token"],
            owner_email,
            oauth_callback._build_store_key("verified@gmail.com"),
        ):
            self.assertNotIn(canary, serialized_payload)

    def test_callback_adopts_legacy_mailbox_owner_after_exact_token_readback(self):
        from urllib.parse import urlencode

        owner_email = "owner-migration-canary@example.invalid"
        old_access = "CANARY_CALLBACK_MAILBOX_OWNER_OLD_ACCESS"
        old_refresh = "CANARY_CALLBACK_MAILBOX_OWNER_OLD_REFRESH"
        fresh_access = "CANARY_CALLBACK_MAILBOX_OWNER_FRESH_ACCESS"
        fresh_refresh = "CANARY_CALLBACK_MAILBOX_OWNER_FRESH_REFRESH"
        second_access = "CANARY_CALLBACK_MAILBOX_OWNER_SECOND_ACCESS"
        authorization_code = "CANARY_CALLBACK_MAILBOX_OWNER_CODE"
        second_authorization_code = "CANARY_CALLBACK_MAILBOX_OWNER_SECOND_CODE"
        request_cookie = "CANARY_CALLBACK_MAILBOX_OWNER_COOKIE"
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token=old_access,
            refresh_token=old_refresh,
        )
        stored_record = json.loads(json.dumps(legacy_record))
        stored_config = self._incomplete_onboarding_config()
        stored_config["email"] = owner_email
        events = []
        state, verifier = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            owner_email,
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        request_path = (
            "/api/inboxes/oauth-callback?"
            + urlencode({"code": authorization_code, "state": state})
        )
        second_state, second_verifier = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            owner_email,
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        second_request_path = (
            "/api/inboxes/oauth-callback?"
            + urlencode(
                {"code": second_authorization_code, "state": second_state}
            )
        )

        class CapturingCallback(FakeHandler):
            def __init__(self, path):
                super().__init__(
                    raw_body=b"",
                    headers={"cookie": request_cookie},
                )
                self.path = path
                self.callback_payload = None

            def _send_callback_page(self, payload, *, set_cookies=()):
                self.callback_payload = payload
                oauth_callback.handler._send_callback_page(
                    self,
                    payload,
                    set_cookies=set_cookies,
                )

        callback = CapturingCallback(request_path)
        second_callback = CapturingCallback(second_request_path)
        logger = Mock()

        def write_and_readback(_config, _store_key, existing_record, record):
            nonlocal stored_record
            events.append("token_write_and_readback")
            self.assertEqual(existing_record, legacy_record)
            self.assertEqual(record["owner_email"], owner_email)
            self.assertEqual(record["access_token"], fresh_access)
            self.assertEqual(record["refresh_token"], fresh_refresh)
            self.assertNotEqual(record["created_at"], legacy_record["created_at"])
            stored_record = json.loads(json.dumps(record))
            return json.loads(json.dumps(stored_record)), None

        def read_token(_config, _store_key):
            return json.loads(json.dumps(stored_record)), None

        def update_and_readback(_config, _store_key, _expected_record, record):
            nonlocal stored_record
            events.append("current_token_write_and_readback")
            self.assertEqual(stored_record["owner_email"], owner_email)
            self.assertEqual(record["access_token"], second_access)
            self.assertEqual(record["refresh_token"], fresh_refresh)
            stored_record = json.loads(json.dumps(record))
            return json.loads(json.dumps(stored_record)), None

        def read_config(_config, _owner_email):
            events.append(
                "config_readback"
                if events and events[-1] == "mailbox_registration"
                else "config_preflight_read"
            )
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_config)),
                "error": None,
            }

        def write_config(_config, _owner_email, expected_record, record):
            nonlocal stored_config
            events.append("mailbox_registration")
            self.assertEqual(expected_record, stored_config)
            stored_config = json.loads(json.dumps(record))
            return {
                "status": "ok",
                "record": json.loads(json.dumps(stored_config)),
                "error": None,
            }

        with patch.dict(
            oauth_callback.os.environ,
            self._production_oauth_environment(),
            clear=True,
        ), patch.object(
            oauth_callback,
            "_GMAIL_CALLBACK_LOGGER",
            logger,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(owner_email), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            side_effect=(
                (
                    {
                        "access_token": fresh_access,
                        "refresh_token": fresh_refresh,
                        "expires_in": 3600,
                    },
                    None,
                ),
                (
                    {
                        "access_token": second_access,
                        "expires_in": 3600,
                    },
                    None,
                ),
            ),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": "verified@gmail.com", "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "_read_durable_record",
            side_effect=read_token,
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_config,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_config,
        ) as config_write, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as create_config, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write, patch.object(
            oauth_callback,
            "_build_gmail_managed_inbox_id",
            return_value="gmail-verified",
        ), patch.object(
            oauth_callback,
            "_adopt_legacy_durable_record",
            side_effect=write_and_readback,
        ) as adoption_write, patch.object(
            oauth_callback,
            "_write_durable_record",
            side_effect=update_and_readback,
        ) as regular_write, patch.object(
            oauth_callback,
            "urlopen",
        ) as external_call:
            oauth_callback.handler.do_GET(callback)
            oauth_callback.handler.do_GET(second_callback)

        self.assertEqual(
            events,
            [
                "config_preflight_read",
                "token_write_and_readback",
                "config_preflight_read",
                "mailbox_registration",
                "config_readback",
                "config_preflight_read",
                "current_token_write_and_readback",
                "config_preflight_read",
                "mailbox_registration",
                "config_readback",
            ],
        )
        adoption_write.assert_called_once()
        regular_write.assert_called_once()
        self.assertEqual(config_write.call_count, 2)
        create_config.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(len(stored_config["managedInboxes"]), 1)
        self.assertEqual(
            stored_config["managedInboxes"][0]["id"],
            "gmail-verified",
        )
        external_call.assert_not_called()
        logger.warning.assert_not_called()
        self.assertEqual(callback.status, 200)
        self.assertEqual(second_callback.status, 200)
        expected_payload = {
            "status": "success",
            "provider": "google",
            "inboxPosition": "main",
            "email": "verified@gmail.com",
            "mailboxId": "gmail-verified",
            "message": (
                "Google account connected. Durable mailbox token storage is active."
            ),
        }
        self.assertEqual(callback.callback_payload, expected_payload)
        self.assertEqual(second_callback.callback_payload, expected_payload)
        response_artifacts = "\n".join(
            (
                callback.wfile.getvalue().decode("utf-8"),
                json.dumps(callback.callback_payload, sort_keys=True),
                json.dumps(callback.response_headers, sort_keys=True),
                second_callback.wfile.getvalue().decode("utf-8"),
                json.dumps(second_callback.callback_payload, sort_keys=True),
                json.dumps(second_callback.response_headers, sort_keys=True),
            )
        )
        for canary in (
            old_access,
            old_refresh,
            fresh_access,
            fresh_refresh,
            second_access,
            owner_email,
            authorization_code,
            second_authorization_code,
            state,
            verifier,
            second_state,
            second_verifier,
            request_cookie,
            oauth_callback._build_store_key("verified@gmail.com"),
        ):
            self.assertNotIn(canary, response_artifacts)
        for forbidden_field in (
            "owner_email",
            "access_token",
            "refresh_token",
            "owner_binding",
        ):
            self.assertNotIn(forbidden_field, response_artifacts)

    def test_legacy_write_readback_and_wrong_owner_fail_before_config(self):
        owner_email = "owner-canary@example.invalid"
        old_access = "CANARY_FAILURE_LEGACY_ACCESS"
        old_refresh = "CANARY_FAILURE_LEGACY_REFRESH"
        fresh_access = "CANARY_FAILURE_FRESH_ACCESS"
        fresh_refresh = "CANARY_FAILURE_FRESH_REFRESH"
        legacy_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token=old_access,
            refresh_token=old_refresh,
        )
        legacy_mailbox_owner_record = durable_google_token(
            owner_email="verified@gmail.com",
            access_token=old_access,
            refresh_token=old_refresh,
        )
        wrong_owner_record = durable_google_token(
            owner_email="other-owner@example.com",
            access_token=old_access,
            refresh_token=old_refresh,
        )
        cases = (
            (
                "wrong_owner",
                wrong_owner_record,
                "token_owner_mismatch",
                None,
            ),
            (
                "write_failure",
                legacy_record,
                "token_persistence_failed",
                "write_failure",
            ),
            (
                "missing_fresh_refresh",
                legacy_record,
                "token_payload_invalid",
                "missing_fresh_refresh",
            ),
            (
                "mailbox_owner_missing_fresh_refresh",
                legacy_mailbox_owner_record,
                "token_payload_invalid",
                "missing_fresh_refresh",
            ),
            (
                "mailbox_owner_write_failure",
                legacy_mailbox_owner_record,
                "token_persistence_failed",
                "write_failure",
            ),
            (
                "mailbox_owner_readback_owner",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("owner_email", "other-owner@example.com"),
            ),
            (
                "mailbox_owner_readback_missing_owner",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                "missing_owner_readback",
            ),
            (
                "mailbox_owner_readback_provider",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("provider", "microsoft"),
            ),
            (
                "mailbox_owner_readback_email",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("email", "other@gmail.com"),
            ),
            (
                "mailbox_owner_readback_old_access",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("access_token", old_access),
            ),
            (
                "mailbox_owner_readback_old_refresh",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("refresh_token", old_refresh),
            ),
            (
                "mailbox_owner_readback_extra_owner_field",
                legacy_mailbox_owner_record,
                "mailbox_readback_verification_failed",
                ("ownerEmail", "verified@gmail.com"),
            ),
            (
                "readback_provider",
                legacy_record,
                "mailbox_readback_verification_failed",
                ("provider", "microsoft"),
            ),
            (
                "readback_email",
                legacy_record,
                "mailbox_readback_verification_failed",
                ("email", "other@gmail.com"),
            ),
            (
                "readback_owner",
                legacy_record,
                "mailbox_readback_verification_failed",
                ("owner_email", "other-owner@example.com"),
            ),
            (
                "readback_stale_access",
                legacy_record,
                "mailbox_readback_verification_failed",
                ("access_token", old_access),
            ),
            (
                "readback_stale_refresh",
                legacy_record,
                "mailbox_readback_verification_failed",
                ("refresh_token", old_refresh),
            ),
        )

        for label, existing_record, expected_failure, write_outcome in cases:
            state, _ = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                owner_email,
                "state-secret",
                "main",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
            callback = self._google_callback(state)
            logger = Mock()
            original_record = json.dumps(existing_record, sort_keys=True)

            callback_token_payload = {"access_token": fresh_access}
            if write_outcome != "missing_fresh_refresh":
                callback_token_payload["refresh_token"] = fresh_refresh

            def write_token(_config, _store_key, old_record, record):
                self.assertEqual(old_record, existing_record)
                if write_outcome == "write_failure":
                    return None, {
                        "code": "token_persistence_failed",
                        "message": "CANARY_PRIVATE_STORAGE_DETAIL",
                    }
                readback = json.loads(json.dumps(record))
                if isinstance(write_outcome, tuple):
                    field, value = write_outcome
                    readback[field] = value
                elif write_outcome == "missing_owner_readback":
                    del readback["owner_email"]
                return readback, None

            with self.subTest(label=label), patch.dict(
                oauth_callback.os.environ,
                self._production_oauth_environment(),
                clear=True,
            ), patch.object(
                oauth_callback,
                "_GMAIL_CALLBACK_LOGGER",
                logger,
            ), patch.object(
                oauth_callback,
                "_resolve_authenticated_member_request",
                return_value=(authenticated_member(owner_email), ()),
            ), patch.object(
                oauth_callback,
                "_exchange_google_code",
                return_value=(
                    callback_token_payload,
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_fetch_verified_google_identity",
                return_value=(
                    {"email": "verified@gmail.com", "display_name": "Verified"},
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_prepare_gmail_managed_inbox_registration",
                return_value=({"prepared": True}, None),
            ), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value={
                    "backend": "vercel_kv_rest",
                    "rest_url": "https://kv.example",
                    "rest_token": "secret",
                },
            ), patch.object(
                oauth_callback,
                "_read_durable_record",
                return_value=(existing_record, None),
            ), patch.object(
                oauth_callback,
                "_adopt_legacy_durable_record",
                side_effect=write_token,
            ) as durable_write, patch.object(
                oauth_callback,
                "_write_durable_record",
            ) as regular_write, patch.object(
                oauth_callback,
                "_register_gmail_managed_inbox_in_user_config",
            ) as config_store:
                oauth_callback.handler.do_GET(callback)

            config_store.assert_not_called()
            regular_write.assert_not_called()
            if label == "wrong_owner" or write_outcome == "missing_fresh_refresh":
                durable_write.assert_not_called()
            else:
                durable_write.assert_called_once()
            self.assertEqual(json.dumps(existing_record, sort_keys=True), original_record)
            logger.warning.assert_called_once_with(
                "event=gmail_oauth_callback_failure "
                f"failure_code={expected_failure} provider=google "
                "inbox_position=main"
            )
            payload = callback._send_callback_page.call_args.args[0]
            self.assertEqual(payload["status"], "error")
            self.assertNotIn("mailboxId", payload)
            combined = "\n".join(
                (
                    logger.warning.call_args.args[0],
                    json.dumps(payload, sort_keys=True),
                )
            )
            for canary in (
                old_access,
                old_refresh,
                fresh_access,
                fresh_refresh,
                owner_email,
                oauth_callback._build_store_key("verified@gmail.com"),
                "CANARY_PRIVATE_STORAGE_DETAIL",
            ):
                self.assertNotIn(canary, combined)

    def test_server_registration_persists_position_and_is_idempotent(self):
        stored_record = self._incomplete_onboarding_config()

        def read_record(_config, _owner_email):
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        def write_record(_config, _owner_email, expected_record, record):
            nonlocal stored_record
            self.assertEqual(expected_record, stored_record)
            stored_record = json.loads(json.dumps(record))
            return {
                "status": "ok",
                "record": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=write_record,
        ) as config_write, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as create_config, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            first_mailbox, first_error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position="main",
                )
            )
            second_mailbox, second_error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="VERIFIED@gmail.com",
                    display_name="Changed display name",
                    owner_email="owner@example.com",
                    message="Reconnected",
                    inbox_position="main",
                )
            )

        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertEqual(config_write.call_count, 2)
        create_config.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(first_mailbox["id"], second_mailbox["id"])
        self.assertEqual(first_mailbox["onboardingInboxId"], "main")
        self.assertEqual(second_mailbox["onboardingInboxId"], "main")
        self.assertEqual(first_mailbox["title"], "Verified")
        self.assertEqual(second_mailbox["title"], "Verified")
        self.assertEqual(len(stored_record["managedInboxes"]), 1)
        saved_mailbox = stored_record["managedInboxes"][0]
        self.assertEqual(saved_mailbox["id"], first_mailbox["id"])
        self.assertEqual(saved_mailbox["email"], "verified@gmail.com")
        self.assertEqual(saved_mailbox["provider"], "google")
        self.assertEqual(saved_mailbox["onboardingInboxId"], "main")
        self.assertTrue(saved_mailbox["connected"])
        serialized_record = json.dumps(stored_record).lower()
        for forbidden in (
            "access_token",
            "refresh_token",
            "ciphertext",
            "tokenreference",
            "oauthauthorizationurl\": \"http",
        ):
            self.assertNotIn(forbidden, serialized_record)

    def test_server_registration_uses_shared_cas_and_preserves_config(self):
        config_a = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "2025-01-01T00:00:00Z",
            "onboardingSession": {
                "schemaVersion": 1,
                "completed": False,
                "currentStep": 3,
                "choices": {
                    "selectedInboxes": ["main", "promo"],
                    "inboxCount": "2",
                    "customInboxes": [],
                    "focusPreferences": {"promo": "high"},
                },
            },
            "managedInboxes": [
                {
                    "id": "gmail-other-server-owned",
                    "email": "other@gmail.com",
                    "provider": "google",
                    "onboardingInboxId": "promo",
                    "connected": True,
                    "connectionStatus": "connected",
                }
            ],
            "mailboxTitleOverrides": {"gmail-other-server-owned": "Pinned"},
            "primaryManagedInboxId": "gmail-other-server-owned",
            "mailboxFocusPreferenceOverrides": {
                "gmail-other-server-owned": "high"
            },
            "inboxSignatures": {
                "gmail-other-server-owned": {"text": "Regards"}
            },
            "smartFolders": [{"id": "important"}],
            "uiPreferences": {"themeMode": "dark"},
            "displayNameOverrides": {"owner@example.com": "Owner"},
            "unrelatedFutureField": {"nested": [1, 2, 3]},
        }
        stored_record = json.loads(json.dumps(config_a))
        events = []

        def read_record(_store, owner_email):
            self.assertEqual(owner_email, "owner@example.com")
            events.append("read")
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        def compare_and_set(_store, owner_email, expected, replacement):
            nonlocal stored_record
            self.assertEqual(owner_email, "owner@example.com")
            events.append("cas")
            self.assertEqual(expected, config_a)
            stored_record = json.loads(json.dumps(replacement))
            return {
                "status": "ok",
                "record": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ) as config_read, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=compare_and_set,
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            mailbox, error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position="main",
                )
            )

        self.assertIsNone(error)
        self.assertEqual(events, ["read", "cas", "read"])
        self.assertEqual(config_read.call_count, 2)
        config_cas.assert_called_once()
        config_create.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(mailbox["email"], "verified@gmail.com")
        self.assertEqual(mailbox["onboardingInboxId"], "main")
        for field in (
            "onboardingSession",
            "mailboxTitleOverrides",
            "primaryManagedInboxId",
            "mailboxFocusPreferenceOverrides",
            "inboxSignatures",
            "smartFolders",
            "uiPreferences",
            "displayNameOverrides",
            "unrelatedFutureField",
        ):
            self.assertEqual(stored_record[field], config_a[field])
        self.assertEqual(
            stored_record["managedInboxes"][0],
            config_a["managedInboxes"][0],
        )
        self.assertEqual(
            [item["id"] for item in stored_record["managedInboxes"]],
            ["gmail-other-server-owned", mailbox["id"]],
        )

    def test_cas_retry_remerges_concurrent_imap_onboarding_and_preferences(self):
        config_a = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "2025-01-01T00:00:00Z",
            "onboardingSession": {
                "schemaVersion": 1,
                "completed": False,
                "currentStep": 2,
                "choices": {
                    "selectedInboxes": ["main"],
                    "inboxCount": "1",
                    "customInboxes": [],
                    "focusPreferences": {"main": "medium"},
                },
            },
            "managedInboxes": [],
            "mailboxTitleOverrides": {},
            "mailboxFocusPreferenceOverrides": {},
            "inboxSignatures": {},
            "uiPreferences": {"themeMode": "light"},
            "unrelatedFutureField": {"revision": "A"},
        }
        config_b = json.loads(json.dumps(config_a))
        config_b["onboardingSession"] = {
            "schemaVersion": 1,
            "completed": False,
            "currentStep": 4,
            "choices": {
                "selectedInboxes": ["main", "custom:tour"],
                "inboxCount": "2",
                "customInboxes": [
                    {"id": "custom:tour", "title": "Tour mailbox"}
                ],
                "focusPreferences": {
                    "main": "low",
                    "custom:tour": "high",
                },
            },
        }
        custom_imap = {
            "id": "imap-server-owned",
            "email": "tour@example.com",
            "provider": "custom_imap",
            "onboardingInboxId": "custom:tour",
            "connected": True,
            "connectionStatus": "connected",
            "customImap": {
                "host": "imap.example.com",
                "port": "993",
                "ssl": True,
                "username": "tour@example.com",
            },
        }
        config_b["managedInboxes"] = [custom_imap]
        config_b["mailboxTitleOverrides"] = {
            "imap-server-owned": "Tour archive"
        }
        config_b["mailboxFocusPreferenceOverrides"] = {
            "imap-server-owned": "high"
        }
        config_b["inboxSignatures"] = {
            "imap-server-owned": {"text": "On tour"}
        }
        config_b["uiPreferences"] = {"themeMode": "dark", "density": "compact"}
        config_b["unrelatedFutureField"] = {"revision": "B", "keep": True}

        stored_record = json.loads(json.dumps(config_a))
        replacements = []
        events = []

        def read_record(_store, _owner_email):
            events.append("read")
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        def compare_and_set(_store, _owner_email, expected, replacement):
            nonlocal stored_record
            replacements.append(json.loads(json.dumps(replacement)))
            if len(replacements) == 1:
                events.append("cas_conflict")
                self.assertEqual(expected, config_a)
                stored_record = json.loads(json.dumps(config_b))
                return {
                    "status": "conflict",
                    "record": None,
                    "error": {
                        "code": "user_config_write_conflict",
                        "message": "concurrent registration",
                    },
                }

            events.append("cas_success")
            self.assertEqual(expected, config_b)
            stored_record = json.loads(json.dumps(replacement))
            return {
                "status": "ok",
                "record": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=compare_and_set,
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            mailbox, error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position="main",
                )
            )

        self.assertIsNone(error)
        self.assertEqual(
            events,
            ["read", "cas_conflict", "read", "cas_success", "read"],
        )
        self.assertEqual(config_cas.call_count, 2)
        config_create.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(len(replacements), 2)
        self.assertNotIn(
            "imap-server-owned",
            [item["id"] for item in replacements[0]["managedInboxes"]],
        )
        self.assertEqual(
            [item["id"] for item in replacements[1]["managedInboxes"]],
            ["imap-server-owned", mailbox["id"]],
        )
        by_id = {
            item["id"]: item for item in stored_record["managedInboxes"]
        }
        self.assertEqual(by_id["imap-server-owned"], custom_imap)
        self.assertEqual(
            by_id[mailbox["id"]]["onboardingInboxId"],
            "main",
        )
        for field in (
            "onboardingSession",
            "mailboxTitleOverrides",
            "mailboxFocusPreferenceOverrides",
            "inboxSignatures",
            "uiPreferences",
            "unrelatedFutureField",
        ):
            self.assertEqual(stored_record[field], config_b[field])

    def test_cas_retry_rejects_concurrent_gmail_owner_change(self):
        config_a = {
            "v": 1,
            "email": "owner@example.com",
            "onboardingSession": self._incomplete_onboarding_config()[
                "onboardingSession"
            ],
            "managedInboxes": [],
        }
        conflicting_mailbox = {
            "id": "gmail-other-owner-server-owned",
            "email": "verified@gmail.com",
            "provider": "google",
            "oauthOwnerEmail": "other-owner@example.com",
            "onboardingInboxId": "main",
            "connected": True,
            "connectionStatus": "connected",
        }
        config_b = {
            **config_a,
            "managedInboxes": [conflicting_mailbox],
            "concurrentRevision": "other owner",
        }
        stored_record = json.loads(json.dumps(config_a))
        read_count = 0

        def read_record(_store, _owner_email):
            nonlocal read_count
            read_count += 1
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        def first_write_conflicts(_store, _owner_email, expected, _replacement):
            nonlocal stored_record
            self.assertEqual(expected, config_a)
            stored_record = json.loads(json.dumps(config_b))
            return {
                "status": "conflict",
                "record": None,
                "error": {
                    "code": "user_config_write_conflict",
                    "message": "other owner won",
                },
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=first_write_conflicts,
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            mailbox, error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position="main",
                )
            )

        self.assertIsNone(mailbox)
        self.assertEqual(error["code"], "gmail_link_conflict")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "gmail_link_conflict",
        )
        self.assertEqual(read_count, 2)
        config_cas.assert_called_once()
        config_create.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(stored_record, config_b)
        self.assertEqual(
            stored_record["managedInboxes"][0]["oauthOwnerEmail"],
            "other-owner@example.com",
        )

    def test_missing_config_create_race_reloads_and_uses_cas(self):
        concurrent_config = {
            "v": 1,
            "email": "owner@example.com",
            "updatedAt": "2025-01-01T00:00:00Z",
            "onboardingSession": {
                "schemaVersion": 1,
                "completed": False,
                "currentStep": 3,
                "choices": {
                    "selectedInboxes": ["main", "custom:legal"],
                    "inboxCount": "2",
                    "customInboxes": [
                        {"id": "custom:legal", "title": "Legal"}
                    ],
                },
            },
            "managedInboxes": [
                {
                    "id": "imap-server-owned",
                    "email": "legal@example.com",
                    "provider": "custom_imap",
                    "onboardingInboxId": "custom:legal",
                    "connected": True,
                    "connectionStatus": "connected",
                }
            ],
            "mailboxFocusPreferenceOverrides": {
                "imap-server-owned": "medium"
            },
        }
        stored_record = None
        read_count = 0
        events = []

        def read_record(_store, _owner_email):
            nonlocal read_count
            read_count += 1
            events.append("read_missing" if stored_record is None else "read")
            if stored_record is None:
                return {
                    "status": "missing",
                    "config": None,
                    "error": {
                        "code": "user_config_not_found",
                        "message": "missing",
                    },
                }
            return {
                "status": "ok",
                "config": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        def create_if_missing(_store, _owner_email, _replacement):
            nonlocal stored_record
            events.append("create_conflict")
            stored_record = json.loads(json.dumps(concurrent_config))
            return {
                "status": "conflict",
                "record": None,
                "error": {
                    "code": "user_config_write_conflict",
                    "message": "concurrent create",
                },
            }

        def compare_and_set(_store, _owner_email, expected, replacement):
            nonlocal stored_record
            events.append("cas_success")
            self.assertEqual(expected, concurrent_config)
            stored_record = json.loads(json.dumps(replacement))
            return {
                "status": "ok",
                "record": json.loads(json.dumps(stored_record)),
                "error": None,
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
            side_effect=create_if_missing,
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=compare_and_set,
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            mailbox, error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position=None,
                )
            )

        self.assertIsNone(error)
        self.assertEqual(
            events,
            [
                "read_missing",
                "create_conflict",
                "read",
                "cas_success",
                "read",
            ],
        )
        self.assertEqual(read_count, 3)
        config_create.assert_called_once()
        config_cas.assert_called_once()
        blind_config_write.assert_not_called()
        by_id = {
            item["id"]: item for item in stored_record["managedInboxes"]
        }
        self.assertEqual(
            by_id["imap-server-owned"],
            concurrent_config["managedInboxes"][0],
        )
        self.assertEqual(by_id[mailbox["id"]]["email"], "verified@gmail.com")
        self.assertEqual(
            stored_record["onboardingSession"],
            concurrent_config["onboardingSession"],
        )
        self.assertEqual(
            stored_record["mailboxFocusPreferenceOverrides"],
            concurrent_config["mailboxFocusPreferenceOverrides"],
        )

    def test_persistent_cas_conflict_stops_after_three_fresh_merges(self):
        revisions = [
            {
                "v": 1,
                "email": "owner@example.com",
                "onboardingSession": self._incomplete_onboarding_config()[
                    "onboardingSession"
                ],
                "managedInboxes": [],
                "concurrentRevision": revision,
            }
            for revision in (1, 2, 3)
        ]
        read_index = 0
        expected_records = []
        replacements = []

        def read_record(_store, _owner_email):
            nonlocal read_index
            record = revisions[read_index]
            read_index += 1
            return {
                "status": "ok",
                "config": json.loads(json.dumps(record)),
                "error": None,
            }

        def always_conflict(_store, _owner_email, expected, replacement):
            expected_records.append(json.loads(json.dumps(expected)))
            replacements.append(json.loads(json.dumps(replacement)))
            return {
                "status": "conflict",
                "record": None,
                "error": {
                    "code": "user_config_write_conflict",
                    "message": "still changing",
                },
            }

        with patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            side_effect=read_record,
        ) as config_read, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            side_effect=always_conflict,
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            mailbox, error = (
                oauth_callback._register_gmail_managed_inbox_in_user_config(
                    authenticated_member(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                    inbox_position="main",
                )
            )

        self.assertIsNone(mailbox)
        self.assertEqual(error["code"], "user_config_persistence_failed")
        self.assertEqual(
            error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
            "user_config_write_failed",
        )
        self.assertEqual(
            config_read.call_count,
            oauth_callback.MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS,
        )
        self.assertEqual(
            config_cas.call_count,
            oauth_callback.MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS,
        )
        config_create.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(expected_records, revisions)
        self.assertEqual(
            [record["concurrentRevision"] for record in replacements],
            [1, 2, 3],
        )
        self.assertTrue(
            all(
                len(record["managedInboxes"]) == 1
                and record["managedInboxes"][0]["email"]
                == "verified@gmail.com"
                for record in replacements
            )
        )
        self.assertTrue(
            all(record["managedInboxes"] == [] for record in revisions)
        )

    def test_invalid_or_unavailable_config_fails_before_any_write(self):
        cases = (
            (
                "unavailable",
                {
                    "status": "unavailable",
                    "config": None,
                    "error": {
                        "code": "user_config_store_unavailable",
                        "message": "private outage",
                    },
                },
                "user_config_preflight_failed",
            ),
            (
                "malformed",
                {
                    "status": "malformed",
                    "config": None,
                    "error": {
                        "code": "user_config_malformed",
                        "message": "private malformed detail",
                    },
                },
                "user_config_invalid",
            ),
            (
                "ok_with_non_dict",
                {"status": "ok", "config": [], "error": None},
                "user_config_invalid",
            ),
            (
                "wrong_owner",
                {
                    "status": "ok",
                    "config": {
                        "email": "other@example.com",
                        "managedInboxes": [],
                    },
                    "error": None,
                },
                "user_config_invalid",
            ),
            (
                "malformed_managed_inboxes",
                {
                    "status": "ok",
                    "config": {
                        "email": "owner@example.com",
                        "onboardingSession": (
                            self._incomplete_onboarding_config()[
                                "onboardingSession"
                            ]
                        ),
                        "managedInboxes": {},
                    },
                    "error": None,
                },
                "user_config_invalid",
            ),
        )

        for name, read_result, diagnostic_code in cases:
            with self.subTest(case=name), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value={
                    "backend": "vercel_kv_rest",
                    "rest_url": "https://kv.example",
                    "rest_token": "secret",
                },
            ), patch.object(
                oauth_callback.user_config_store,
                "read_user_config_record",
                return_value=read_result,
            ), patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_unchanged",
            ) as config_cas, patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_missing",
            ) as config_create, patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record",
            ) as blind_config_write:
                mailbox, error = (
                    oauth_callback._register_gmail_managed_inbox_in_user_config(
                        authenticated_member(),
                        email="verified@gmail.com",
                        display_name="Verified",
                        owner_email="owner@example.com",
                        message="Connected",
                        inbox_position="main",
                    )
                )

            self.assertIsNone(mailbox)
            self.assertEqual(error["code"], "user_config_persistence_failed")
            self.assertEqual(
                error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
                diagnostic_code,
            )
            config_cas.assert_not_called()
            config_create.assert_not_called()
            blind_config_write.assert_not_called()
            self.assertNotIn("private", json.dumps(error))

    def test_server_mailbox_id_avoids_case_insensitive_collision(self):
        mailbox_id = oauth_callback._build_gmail_managed_inbox_id(
            "foo@example.com",
            {"GMAIL-FOO"},
        )
        self.assertEqual(mailbox_id, "gmail-foo-example-com")
        self.assertNotEqual(mailbox_id.casefold(), "GMAIL-FOO".casefold())

    def test_server_registration_rejects_both_position_conflict_directions(self):
        existing = [
            {
                "id": "gmail-verified",
                "email": "verified@gmail.com",
                "provider": "google",
                "onboardingInboxId": "main",
                "connected": True,
                "connectionStatus": "connected",
            }
        ]
        cases = (
            ("same_position_other_mailbox", "other@gmail.com", "main"),
            ("same_mailbox_other_position", "verified@gmail.com", "demo"),
        )

        for label, email, inbox_position in cases:
            with self.subTest(label=label):
                matched_index, error = (
                    oauth_callback._resolve_gmail_managed_inbox_target(
                        existing,
                        email=email,
                        owner_email="owner@example.com",
                        inbox_position=inbox_position,
                    )
                )
            self.assertIsNone(matched_index)
            self.assertEqual(error["code"], "gmail_link_conflict")

    def test_server_registration_rejects_conflicting_or_malformed_mailbox_owner(self):
        for stored_owner in (
            "other-owner@example.com",
            "",
            True,
            ["owner@example.com"],
        ):
            existing = [
                {
                    "id": "gmail-verified-server-owned",
                    "email": "verified@gmail.com",
                    "provider": "google",
                    "oauthOwnerEmail": stored_owner,
                    "onboardingInboxId": "main",
                    "connected": True,
                    "connectionStatus": "connected",
                }
            ]
            with self.subTest(stored_owner=stored_owner):
                matched_index, error = (
                    oauth_callback._resolve_gmail_managed_inbox_target(
                        existing,
                        email="verified@gmail.com",
                        owner_email="owner@example.com",
                        inbox_position="main",
                    )
                )
            self.assertIsNone(matched_index)
            self.assertEqual(error["code"], "gmail_link_conflict")

    def test_callback_registration_conflict_stops_before_token_write(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = self._google_callback(state)
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
        }

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=False,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=({"access_token": "provider-token"}, None),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": "verified@gmail.com", "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
            return_value=(
                None,
                {
                    "code": "gmail_link_conflict",
                    "message": "Position already linked.",
                },
            ),
        ), patch.object(
            oauth_callback,
            "persist_google_token_record",
        ) as token_store, patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
        ) as config_store:
            oauth_callback.handler.do_GET(callback)

        token_store.assert_not_called()
        config_store.assert_not_called()
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["inboxPosition"], "main")
        self.assertEqual(response["email"], "verified@gmail.com")
        self.assertNotIn("mailboxId", response)
        self.assertNotIn("provider-token", json.dumps(response))

    def test_callback_revalidates_current_onboarding_and_member_authority(self):
        environment = self._production_oauth_environment()
        active_config = self._incomplete_onboarding_config()
        occupied_config = self._incomplete_onboarding_config()
        occupied_config["managedInboxes"] = [
            {
                "id": "gmail-other-server-owned",
                "email": "other@gmail.com",
                "provider": "google",
                "oauthOwnerEmail": "owner@example.com",
                "onboardingInboxId": "main",
                "connected": True,
                "connectionStatus": "connected",
            }
        ]
        original_member = authenticated_member()
        changed_user_member = auth_runtime.AuthenticatedMemberContext(
            user_id="different-user",
            email=original_member.email,
            name=original_member.name,
            workspace_id=original_member.workspace_id,
            membership_role=original_member.membership_role,
        )
        changed_workspace_member = auth_runtime.AuthenticatedMemberContext(
            user_id=original_member.user_id,
            email=original_member.email,
            name=original_member.name,
            workspace_id="different-workspace",
            membership_role=original_member.membership_role,
        )
        cases = (
            (
                "position_removed",
                self._incomplete_onboarding_config(
                    selected_inboxes=("demo",)
                ),
                original_member,
                "gmail_link_conflict",
            ),
            (
                "onboarding_completed",
                self._incomplete_onboarding_config(completed=True),
                original_member,
                "gmail_link_conflict",
            ),
            (
                "position_occupied",
                occupied_config,
                original_member,
                "gmail_link_conflict",
            ),
            (
                "account_binding_changed",
                active_config,
                changed_user_member,
                "owner_binding_invalid",
            ),
            (
                "workspace_binding_changed",
                active_config,
                changed_workspace_member,
                "owner_binding_invalid",
            ),
        )

        for label, current_config, current_member, expected_failure in cases:
            state, _ = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                original_member.email,
                "state-secret",
                "main",
                member_user_id=original_member.user_id,
                member_workspace_id=original_member.workspace_id,
            )
            callback = self._google_callback(state)
            logger = Mock()
            with self.subTest(case=label), patch.dict(
                oauth_callback.os.environ,
                environment,
                clear=True,
            ), patch.object(
                oauth_callback,
                "_GMAIL_CALLBACK_LOGGER",
                logger,
            ), patch.object(
                oauth_callback,
                "_resolve_authenticated_member_request",
                side_effect=(
                    (original_member, ()),
                    (current_member, ()),
                ),
            ), patch.object(
                oauth_callback,
                "_exchange_google_code",
                return_value=(
                    {
                        "access_token": "private-provider-access",
                        "refresh_token": "private-provider-refresh",
                    },
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_fetch_verified_google_identity",
                return_value=(
                    {
                        "email": "verified@gmail.com",
                        "display_name": "Verified",
                    },
                    None,
                ),
            ), patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value={
                    "backend": "vercel_kv_rest",
                    "rest_url": "https://kv.example",
                    "rest_token": "secret",
                },
            ), patch.object(
                oauth_callback.user_config_store,
                "read_user_config_record",
                return_value={
                    "status": "ok",
                    "config": current_config,
                    "error": None,
                },
            ) as config_read, patch.object(
                oauth_callback,
                "persist_google_token_record",
            ) as token_store, patch.object(
                oauth_callback,
                "_register_gmail_managed_inbox_in_user_config",
            ) as config_store, patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_unchanged",
            ) as config_cas, patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_missing",
            ) as config_create:
                oauth_callback.handler.do_GET(callback)

            token_store.assert_not_called()
            config_store.assert_not_called()
            config_cas.assert_not_called()
            config_create.assert_not_called()
            if expected_failure == "owner_binding_invalid":
                config_read.assert_not_called()
            else:
                config_read.assert_called_once()
            logger.warning.assert_called_once_with(
                "event=gmail_oauth_callback_failure "
                f"failure_code={expected_failure} provider=google "
                "inbox_position=main"
            )
            response = callback._send_callback_page.call_args.args[0]
            self.assertEqual(response["status"], "error")
            self.assertNotIn("mailboxId", response)
            self.assertNotIn("connected", response)
            serialized_response = json.dumps(response, sort_keys=True)
            for forbidden in (
                "private-provider-access",
                "private-provider-refresh",
                "owner_binding",
                "userId",
                "workspaceId",
            ):
                self.assertNotIn(forbidden, serialized_response)

    def test_config_registration_failure_never_emits_callback_success(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = self._google_callback(state)
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
        }

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=False,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=({"access_token": "provider-token"}, None),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": "verified@gmail.com", "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_prepare_gmail_managed_inbox_registration",
            return_value=({"prepared": True}, None),
        ), patch.object(
            oauth_callback,
            "persist_google_token_record",
            return_value=({"_storage_durable": True}, None),
        ) as token_store, patch.object(
            oauth_callback,
            "_register_gmail_managed_inbox_in_user_config",
            return_value=(
                None,
                {
                    "code": "user_config_persistence_failed",
                    "message": "raw storage detail",
                },
            ),
        ) as config_store:
            oauth_callback.handler.do_GET(callback)

        token_store.assert_called_once()
        config_store.assert_called_once()
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["inboxPosition"], "main")
        self.assertEqual(response["email"], "verified@gmail.com")
        self.assertNotIn("mailboxId", response)
        self.assertNotIn("raw storage detail", json.dumps(response))
        self.assertNotIn("provider-token", json.dumps(response))

    def test_persistent_cas_conflict_keeps_token_pending_and_callback_failed(self):
        state, _ = connect_oauth.build_signed_state(
            "google",
            "hint@gmail.com",
            "owner@example.com",
            "state-secret",
            "main",
            member_user_id="user-1",
            member_workspace_id="workspace-1",
        )
        callback = self._google_callback(state)
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
        }
        config = {
            "v": 1,
            "email": "owner@example.com",
            "onboardingSession": self._incomplete_onboarding_config()[
                "onboardingSession"
            ],
            "managedInboxes": [],
        }
        logger = Mock()

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=False,
        ), patch.object(
            oauth_callback,
            "_GMAIL_CALLBACK_LOGGER",
            logger,
        ), patch.object(
            oauth_callback,
            "_resolve_authenticated_member_request",
            return_value=(authenticated_member(), ()),
        ), patch.object(
            oauth_callback,
            "_exchange_google_code",
            return_value=({"access_token": "provider-token"}, None),
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=(
                {"email": "verified@gmail.com", "display_name": "Verified"},
                None,
            ),
        ), patch.object(
            oauth_callback,
            "_resolve_durable_store_config",
            return_value={
                "backend": "vercel_kv_rest",
                "rest_url": "https://kv.example",
                "rest_token": "secret",
            },
        ), patch.object(
            oauth_callback,
            "persist_google_token_record",
            return_value=(
                {
                    "_storage_durable": True,
                    "provider": "google",
                    "email": "verified@gmail.com",
                    "owner_email": "owner@example.com",
                },
                None,
            ),
        ) as token_store, patch.object(
            oauth_callback.user_config_store,
            "read_user_config_record",
            return_value={
                "status": "ok",
                "config": config,
                "error": None,
            },
        ) as config_read, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_unchanged",
            return_value={
                "status": "conflict",
                "record": None,
                "error": {
                    "code": "user_config_write_conflict",
                    "message": "still changing",
                },
            },
        ) as config_cas, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record_if_missing",
        ) as config_create, patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            oauth_callback.handler.do_GET(callback)

        token_store.assert_called_once()
        self.assertEqual(
            config_read.call_count,
            1 + oauth_callback.MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS,
        )
        self.assertEqual(
            config_cas.call_count,
            oauth_callback.MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS,
        )
        config_create.assert_not_called()
        blind_config_write.assert_not_called()
        self.assertEqual(config["managedInboxes"], [])
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["inboxPosition"], "main")
        self.assertEqual(response["email"], "verified@gmail.com")
        self.assertNotIn("mailboxId", response)
        self.assertNotIn("provider-token", json.dumps(response))
        self.assertNotIn("connected", response)
        self.assertEqual(
            logger.warning.call_args.args[0],
            "event=gmail_oauth_callback_failure "
            "failure_code=user_config_write_failed provider=google "
            "inbox_position=main",
        )

    def test_config_write_uses_real_shared_eval_cas_and_exact_readback(self):
        environment = {
            "KV_REST_API_URL": "https://kv.example",
            "KV_REST_API_TOKEN": "kv-secret",
        }
        stored_record = {
            "v": 1,
            "email": "owner@example.com",
            "managedInboxes": [],
            "preserved": {"value": True},
        }
        calls = []
        commands = []

        def transport(request, timeout):
            nonlocal stored_record
            self.assertEqual(timeout, 20)
            calls.append((request.get_method(), request.full_url))
            if request.get_method() == "GET":
                return BoundaryResponse(
                    json.dumps({"result": json.dumps(stored_record)})
                )

            command = json.loads(request.data)
            commands.append(command)
            self.assertEqual(request.full_url, "https://kv.example")
            self.assertEqual(command[0], "EVAL")
            self.assertEqual(command[2], 1)
            self.assertEqual(
                command[3],
                oauth_callback.user_config_store.build_user_config_key(
                    "owner@example.com"
                ),
            )
            self.assertEqual(json.loads(command[4]), stored_record)
            stored_record = json.loads(command[5])
            return BoundaryResponse('{"result":"saved"}')

        with patch.dict(
            oauth_callback.os.environ,
            environment,
            clear=False,
        ), patch.object(
            oauth_callback.user_config_store,
            "urlopen",
            side_effect=transport,
        ), patch.object(
            oauth_callback.user_config_store,
            "write_user_config_record",
        ) as blind_config_write:
            error = oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                authenticated_member(),
                email="verified@gmail.com",
                display_name="Verified",
                owner_email="owner@example.com",
                message="Connected",
            )

        self.assertIsNone(error)
        self.assertEqual(
            [method for method, _url in calls],
            ["GET", "POST", "GET"],
        )
        self.assertEqual(len(commands), 1)
        blind_config_write.assert_not_called()
        self.assertEqual(stored_record["preserved"], {"value": True})
        self.assertEqual(
            stored_record["managedInboxes"][0]["email"],
            "verified@gmail.com",
        )

    def test_config_readback_rejects_missing_stale_email_and_owner(self):
        base_config = {
            "v": 1,
            "email": "owner@example.com",
            "managedInboxes": [],
            "preservedAfterCas": {
                "customImapMailboxId": "imap-server-owned",
                "preferences": {"focus": "high"},
            },
        }

        def run_with_readback(readback_builder):
            replacement = None
            read_count = 0

            def read_record(_store, _owner_email):
                nonlocal read_count
                read_count += 1
                if read_count == 1:
                    return {
                        "status": "ok",
                        "config": json.loads(json.dumps(base_config)),
                        "error": None,
                    }
                return readback_builder(
                    json.loads(json.dumps(replacement))
                )

            def compare_and_set(
                _store,
                _owner_email,
                expected,
                next_record,
            ):
                nonlocal replacement
                self.assertEqual(expected, base_config)
                replacement = json.loads(json.dumps(next_record))
                return {
                    "status": "ok",
                    "record": replacement,
                    "error": None,
                }

            with patch.object(
                oauth_callback,
                "_resolve_durable_store_config",
                return_value={
                    "backend": "vercel_kv_rest",
                    "rest_url": "https://kv.example",
                    "rest_token": "secret",
                },
            ), patch.object(
                oauth_callback.user_config_store,
                "read_user_config_record",
                side_effect=read_record,
            ), patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_unchanged",
                side_effect=compare_and_set,
            ), patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record_if_missing",
            ) as config_create, patch.object(
                oauth_callback.user_config_store,
                "write_user_config_record",
            ) as blind_config_write:
                result = (
                    oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                        authenticated_member(),
                        email="verified@gmail.com",
                        display_name="Verified",
                        owner_email="owner@example.com",
                        message="Connected",
                    )
                )

            config_create.assert_not_called()
            blind_config_write.assert_not_called()
            return result

        def without_mailbox(record):
            record["managedInboxes"] = []
            return {"status": "ok", "config": record, "error": None}

        def stale_timestamp(record):
            record["updatedAt"] = "2000-01-01T00:00:00Z"
            return {"status": "ok", "config": record, "error": None}

        def altered_mailbox(field, value):
            def build(record):
                record["managedInboxes"][0][field] = value
                return {"status": "ok", "config": record, "error": None}

            return build

        def drops_unrelated_field(record):
            record.pop("preservedAfterCas")
            return {"status": "ok", "config": record, "error": None}

        cases = (
            (
                "missing",
                lambda _record: {
                    "status": "missing",
                    "config": None,
                    "error": None,
                },
                "user_config_readback_failed",
            ),
            (
                "unavailable",
                lambda _record: {
                    "status": "unavailable",
                    "config": None,
                    "error": {
                        "code": "user_config_store_unavailable",
                        "message": "private outage",
                    },
                },
                "user_config_readback_failed",
            ),
            (
                "missing_mailbox",
                without_mailbox,
                "mailbox_readback_verification_failed",
            ),
            (
                "stale_timestamp",
                stale_timestamp,
                "mailbox_readback_verification_failed",
            ),
            (
                "dropped_unrelated_field",
                drops_unrelated_field,
                "mailbox_readback_verification_failed",
            ),
            (
                "wrong_email",
                altered_mailbox("email", "other@gmail.com"),
                "mailbox_readback_verification_failed",
            ),
            (
                "wrong_owner",
                altered_mailbox("oauthOwnerEmail", "other@example.com"),
                "mailbox_readback_verification_failed",
            ),
        )
        for name, builder, diagnostic_code in cases:
            with self.subTest(case=name):
                error = run_with_readback(builder)
            self.assertEqual(error["code"], "user_config_persistence_failed")
            self.assertEqual(
                error[oauth_callback.GMAIL_CALLBACK_FAILURE_CODE_FIELD],
                diagnostic_code,
            )
            self.assertNotIn("private", json.dumps(error))

    def test_real_callback_transport_chain_requires_config_persistence_before_success(self):
        environment = {
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "CUEVION_APP_URL": "https://app.cuevion.com",
            "VERCEL_ENV": "production",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
            "KV_REST_API_URL": "https://kv.example",
            "KV_REST_API_TOKEN": "kv-secret",
        }

        def run_callback(*, config_ack="saved"):
            state, _ = connect_oauth.build_signed_state(
                "google",
                "hint@gmail.com",
                "owner@example.com",
                "state-secret",
                member_user_id="user-1",
                member_workspace_id="workspace-1",
            )
            callback = Mock()
            callback.path = f"/api/inboxes/oauth-callback?code=provider-code&state={state}"
            callback.headers = self._authenticated_headers()
            callback._send_callback_page = Mock()
            token_record = None
            config_record = None

            def transport(request, timeout):
                nonlocal token_record, config_record
                url = request.full_url
                if url == oauth_callback.GOOGLE_TOKEN_ENDPOINT:
                    return BoundaryResponse(
                        json.dumps(
                            {
                                "access_token": "access-secret",
                                "refresh_token": "refresh-secret",
                                "expires_in": 3600,
                            }
                        )
                    )
                if url == oauth_callback.GOOGLE_USERINFO_ENDPOINT:
                    return BoundaryResponse(
                        json.dumps(
                            {
                                "email": "verified@gmail.com",
                                "email_verified": True,
                                "name": "Verified",
                            }
                        )
                    )
                if "cuevion%3Agmail%3Aoauthtoken" in url:
                    self.assertEqual(request.get_method(), "GET")
                    return BoundaryResponse(
                        json.dumps(
                            {"result": json.dumps(token_record) if token_record else None}
                        )
                    )
                if (
                    url == "https://kv.example"
                    and request.get_method() == "POST"
                ):
                    command = json.loads(request.data)
                    self.assertEqual(command[0], "EVAL")
                    self.assertEqual(command[2], 1)
                    if command[3].startswith(
                        "cuevion:gmail:oauthtoken:"
                    ):
                        self.assertEqual(
                            command[1],
                            oauth_callback.GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
                        )
                        if token_record is not None:
                            return BoundaryResponse('{"result":0}')
                        token_record = json.loads(command[4])
                        return BoundaryResponse('{"result":1}')
                    self.assertEqual(
                        command[3],
                        oauth_callback.user_config_store.build_user_config_key(
                            "owner@example.com"
                        ),
                    )
                    if config_ack == "saved":
                        config_record = json.loads(command[4])
                    return BoundaryResponse(
                        json.dumps({"result": config_ack})
                    )
                if "cuevion%3Auser%3Av1" in url:
                    self.assertEqual(request.get_method(), "GET")
                    return BoundaryResponse(
                        json.dumps(
                            {"result": json.dumps(config_record) if config_record else None}
                        )
                    )
                raise AssertionError(f"Unexpected mocked transport URL: {url}")

            with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
                oauth_callback,
                "_resolve_authenticated_member_request",
                return_value=(authenticated_member(), ()),
            ), patch.object(
                oauth_callback,
                "urlopen",
                side_effect=transport,
            ), patch.object(
                oauth_callback.user_config_store,
                "urlopen",
                side_effect=transport,
            ):
                oauth_callback.handler.do_GET(callback)
            return callback._send_callback_page.call_args.args[0], token_record, config_record

        success, token_record, config_record = run_callback()
        self.assertEqual(success["status"], "success")
        self.assertEqual(success["provider"], "google")
        self.assertEqual(success["email"], "verified@gmail.com")
        self.assertEqual(success["mailboxId"], config_record["managedInboxes"][0]["id"])
        self.assertEqual(token_record["owner_email"], "owner@example.com")
        self.assertEqual(config_record["managedInboxes"][0]["email"], "verified@gmail.com")
        self.assertEqual(
            config_record["managedInboxes"][0]["oauthOwnerEmail"],
            "owner@example.com",
        )

        failure, _, _ = run_callback(config_ack="exists")
        self.assertEqual(failure["status"], "error")
        self.assertNotIn("mailboxId", failure)
        self.assertNotIn("exists", json.dumps(failure))
        self.assertNotIn("access-secret", json.dumps(failure))

    def test_public_config_cannot_claim_or_replace_google_identity(self):
        existing = {
            "managedInboxes": [{
                **inbox(),
                "connectionMethod": "oauth",
                "connectionType": "oauth",
                "oauthOwnerEmail": "owner@example.com",
                "title": "Old",
            }]
        }
        attempted = [{
            **inbox(email="attacker@gmail.com", provider="custom_imap", connected=False),
            "id": "gmail-1",
            "connectionStatus": "connection_failed",
            "title": "New",
        }, {**inbox(), "id": "claimed-google"}]
        update = config_route._sanitize_user_config(
            {"managedInboxes": attempted}, "owner@example.com"
        )
        merged = config_route._merge_user_config(existing, update)
        self.assertEqual(len(merged["managedInboxes"]), 1)
        preserved = merged["managedInboxes"][0]
        self.assertEqual(preserved["email"], "verified@gmail.com")
        self.assertEqual(preserved["provider"], "google")
        self.assertTrue(preserved["connected"])
        self.assertEqual(preserved["title"], "New")

        custom = {"managedInboxes": [{"id": "imap-1", "provider": "custom_imap", "email": "a@example.com"}]}
        merged_custom = config_route._merge_user_config(
            None, config_route._sanitize_user_config(custom, "owner@example.com")
        )
        self.assertEqual(merged_custom["managedInboxes"], [])

    def test_public_config_deduplicates_google_ids_and_copies_only_safe_fields(self):
        existing_google = {
            **inbox(),
            "connectionMethod": "oauth",
            "connectionType": "oauth",
            "oauthOwnerEmail": "owner@example.com",
            "serverVerified": True,
            "title": "Old title",
            "internalRole": "dj",
            "focusPreferences": {"promo": "medium"},
        }
        requested = [
            {
                "id": " GMAIL-1 ",
                "email": "victim@gmail.com",
                "provider": "custom_imap",
                "connected": False,
                "connectionStatus": "connection_failed",
                "oauthOwnerEmail": "other@example.com",
                "serverVerified": False,
                "unknownField": "must-not-survive",
                "customSmtp": {"host": "evil.example"},
                "title": "New title",
                "internalRole": "producer",
                "focusPreferences": {"promo": "low"},
            },
        ]
        merged = config_route._merge_server_owned_managed_inboxes(
            [existing_google], requested
        )
        self.assertEqual(len(merged), 1)
        saved = merged[0]
        self.assertEqual(saved["id"], "gmail-1")
        self.assertEqual(saved["email"], "verified@gmail.com")
        self.assertEqual(saved["provider"], "google")
        self.assertTrue(saved["connected"])
        self.assertEqual(saved["connectionStatus"], "connected")
        self.assertEqual(saved["oauthOwnerEmail"], "owner@example.com")
        self.assertTrue(saved["serverVerified"])
        self.assertEqual(saved["title"], "New title")
        self.assertEqual(saved["internalRole"], "producer")
        self.assertEqual(saved["focusPreferences"], {"promo": "low"})
        self.assertNotIn("unknownField", saved)
        self.assertNotIn("customSmtp", saved)

        ambiguous = config_route._merge_server_owned_managed_inboxes(
            [existing_google],
            [
                {"id": "gmail-1", "title": "Duplicate one"},
                {"id": "GMAIL-1", "title": "Duplicate two"},
            ],
        )
        self.assertEqual(ambiguous, [existing_google])

        custom = {"id": "imap-1", "provider": "custom_imap", "custom": {"x": 1}}
        custom_result = config_route._merge_server_owned_managed_inboxes(
            [existing_google], [custom]
        )
        self.assertEqual(custom_result, [existing_google])

    def test_protected_google_client_fields_require_exact_types_and_shapes(self):
        self.assertEqual(
            config_route.SUPPORTED_INTERNAL_ROLES,
            {
                "management",
                "label_manager",
                "label_ar_manager",
                "ar_manager",
                "product_manager",
                "artist_manager",
                "dj",
                "producer",
            },
        )
        existing_google = {
            **inbox(),
            "connectionMethod": "oauth",
            "connectionType": "oauth",
            "oauthOwnerEmail": "owner@example.com",
            "serverVerified": True,
            "title": "Existing title",
            "internalRole": "management",
            "focusPreferences": {"promo": "medium"},
        }
        invalid_updates = (
            {"title": {"nested": "value"}},
            {"title": ["array"]},
            {"title": 123},
            {"title": "   "},
            {"title": "x" * (config_route.MAX_MANAGED_INBOX_TITLE_LENGTH + 1)},
            {"title": "unsafe\u0000title"},
            {"internalRole": ["dj"]},
            {"internalRole": True},
            {"internalRole": "unknown_role"},
            {"focusPreferences": None},
            {"focusPreferences": {"promo": "low", "legal": "urgent"}},
            {"focusPreferences": {"unknown": "low"}},
            {"focusPreferences": {"promo": {"nested": "low"}}},
        )
        for update in invalid_updates:
            with self.subTest(update=update):
                merged = config_route._merge_server_owned_managed_inboxes(
                    [existing_google],
                    [{"id": " GMAIL-1 ", **update}],
                )
            self.assertEqual(merged, [existing_google])

        valid_updates = (
            ({"title": "  New title  "}, "title", "New title"),
            ({"internalRole": "producer"}, "internalRole", "producer"),
            ({"internalRole": None}, "internalRole", None),
            (
                {"focusPreferences": {"promo": "low", "demos": "high"}},
                "focusPreferences",
                {"promo": "low", "demos": "high"},
            ),
        )
        for update, field, expected in valid_updates:
            with self.subTest(update=update):
                merged = config_route._merge_server_owned_managed_inboxes(
                    [existing_google],
                    [{"id": "gmail-1", **update}],
                )
            self.assertEqual(merged[0][field], expected)
            self.assertEqual(merged[0]["email"], "verified@gmail.com")
            self.assertEqual(merged[0]["provider"], "google")
            self.assertEqual(merged[0]["oauthOwnerEmail"], "owner@example.com")
            self.assertTrue(merged[0]["connected"])

        custom = {
            "id": "imap-1",
            "provider": "custom_imap",
            "title": {"custom": "unchanged"},
        }
        self.assertEqual(
            config_route._merge_server_owned_managed_inboxes(
                [existing_google], [custom]
            ),
            [existing_google],
        )

    def test_imports_are_side_effect_free(self):
        with patch.object(authenticated_gmail, "resolve_owned_managed_inbox_record") as ownership, patch.object(
            authenticated_gmail, "load_google_token_record_with_metadata"
        ) as token_load, patch.object(authenticated_gmail, "refresh_google_token_record") as refresh:
            importlib.reload(authenticated_gmail)
        ownership.assert_not_called()
        token_load.assert_not_called()
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
