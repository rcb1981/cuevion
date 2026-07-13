import importlib.util
import base64
import io
import json
import sys
import unittest
from contextlib import ExitStack
from email.errors import MessageError
from email.message import EmailMessage
from email import message_from_bytes
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = API_DIR.parent
for directory in (CURRENT_DIR, API_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import authenticated_gmail
import beta_auth
import imap_connect_preview
import oauth_token_store


def load_route(filename, name):
    spec = importlib.util.spec_from_file_location(name, CURRENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


connect_oauth = load_route("connect-oauth.py", "connect_oauth_ownership_test")
oauth_callback = load_route("oauth-callback.py", "oauth_callback_ownership_test")
config_route = load_route("../user/config.py", "user_config_google_ownership_test")
fetch_gmail = load_route("fetch-gmail.py", "fetch_gmail_ownership_test")
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


class FakeHandler:
    def __init__(self, payload=None, raw_body=None, headers=None):
        body = raw_body if raw_body is not None else json.dumps(payload or {}).encode()
        self.headers = {"content-length": str(len(body)), **(headers or {})}
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
        "CUEVION_BETA_SESSION_SECRET": "session-secret",
        "KV_REST_API_URL": "https://kv.example",
        "KV_REST_API_TOKEN": "kv-secret",
        "GOOGLE_CLIENT_ID": "client-id",
        "GOOGLE_CLIENT_SECRET": "client-secret",
    }

    def _session_cookie(self, email="owner@example.com"):
        with patch.dict(beta_auth.os.environ, self.environment, clear=False):
            value = beta_auth.build_beta_session_token(name="Owner", email=email)
        return f"cuevion_beta_session={value}"

    def _route_cases(self):
        raw_message = base64.urlsafe_b64encode(
            b"From: sender@example.com\r\nTo: owner@example.com\r\nSubject: Empty\r\n\r\nBody"
        ).rstrip(b"=").decode()
        return [
            (fetch_gmail, {"mailboxId": "gmail-1"}, {"messages": []}, 200),
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
                {},
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
            stack.enter_context(patch.dict(beta_auth.os.environ, self.environment, clear=False))
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
            {},
        )
        provider_request = provider_transport.call_args.args[0]
        encoded_message = json.loads(provider_request.data)["raw"]
        decoded_message = base64.urlsafe_b64decode(
            encoded_message + "=" * (-len(encoded_message) % 4)
        )
        self.assertEqual(
            message_from_bytes(decoded_message).get("From"),
            "verified@gmail.com",
        )

    def test_each_route_rejects_missing_session_and_other_users_mailbox(self):
        with patch.dict(beta_auth.os.environ, self.environment, clear=False), patch.object(
            beta_auth.time,
            "time",
            return_value=100,
        ):
            expired = beta_auth.build_beta_session_token(
                name="Expired", email="owner@example.com"
            )
        invalid_cookies = ("", "cuevion_beta_session=malformed", f"cuevion_beta_session={expired}")
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
                stack.enter_context(patch.dict(beta_auth.os.environ, environment, clear=False))
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
                BoundaryResponse(json.dumps({"id": "good", "raw": valid_raw, "labelIds": []})),
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

    def test_fetch_skips_only_documented_message_parse_errors(self):
        malformed_raw = base64.urlsafe_b64encode(b"malformed provider message").rstrip(b"=").decode()
        valid_raw = base64.urlsafe_b64encode(
            b"From: sender@example.com\r\nTo: owner@example.com\r\nSubject: Valid\r\n\r\nBody"
        ).rstrip(b"=").decode()
        provider_transport = Mock(
            side_effect=(
                BoundaryResponse(json.dumps({"messages": [{"id": "bad"}, {"id": "good"}]})),
                BoundaryResponse(json.dumps({"id": "bad", "raw": malformed_raw, "labelIds": []})),
                BoundaryResponse(json.dumps({"id": "good", "raw": valid_raw, "labelIds": []})),
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
                BoundaryResponse(json.dumps({"id": "first", "raw": first_raw, "labelIds": []})),
                BoundaryResponse(json.dumps({"id": "second", "raw": second_raw, "labelIds": []})),
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
                    BoundaryResponse(json.dumps({"id": "first", "raw": first_raw, "labelIds": []})),
                    BoundaryResponse(json.dumps({"id": "second", "raw": second_raw, "labelIds": []})),
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
        with patch.dict(beta_auth.os.environ, self.environment, clear=False), patch.object(
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
        return self.matrix._invoke(send_gmail, payload, {})

    def assert_accepted(self, payload):
        request, config_transport, token_transport, provider_transport = self.invoke(payload)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload(), {"ok": True})
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
            beta_auth.os.environ,
            self.matrix.environment,
            clear=False,
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
        with patch.dict(
            beta_auth.os.environ,
            {"CUEVION_BETA_SESSION_SECRET": "session-secret"},
            clear=False,
        ):
            session = beta_auth.build_beta_session_token(name="Owner", email=email)
        return {"cookie": f"cuevion_beta_session={session}"}

    def test_connect_oauth_requires_session_and_signed_state_has_owner(self):
        handler = FakeHandler({"provider": "google", "email": "hint@gmail.com"})
        with patch.object(connect_oauth, "resolve_authenticated_user", return_value=(None, None)):
            connect_oauth.handler.do_POST(handler)
        self.assertEqual(handler.status, 401)
        self.assertEqual(handler.payload()["error"]["code"], "unauthorized")

        state, verifier = connect_oauth.build_signed_state(
            "google", "hint@gmail.com", "Owner@Example.com", "state-secret"
        )
        encoded = state.split(".", 1)[0]
        padded = encoded + "=" * (-len(encoded) % 4)
        import base64
        payload = json.loads(base64.urlsafe_b64decode(padded))
        self.assertNotIn("owner_email", payload)
        self.assertNotIn("owner@example.com", json.dumps(payload))
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
            )
        )
        self.assertFalse(
            oauth_callback.verify_owner_binding(
                verified,
                "other@example.com",
                "state-secret",
            )
        )

    def test_authenticated_oauth_start_uses_real_session_and_opaque_state(self):
        headers = self._authenticated_headers()
        request = FakeHandler(
            {"provider": "google", "email": "hint@gmail.com"},
            headers=headers,
        )
        environment = {
            "CUEVION_BETA_SESSION_SECRET": "session-secret",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
        }
        with patch.dict(connect_oauth.os.environ, environment, clear=False):
            connect_oauth.handler.do_POST(request)
        self.assertEqual(request.status, 200)
        authorization_url = request.payload()["authorizationUrl"]
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        encoded = state.split(".", 1)[0]
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertNotIn("owner_email", decoded)
        self.assertNotIn("owner@example.com", json.dumps(decoded))

    def test_state_expiry_tampering_context_binding_and_pkce_are_stable(self):
        with patch.object(connect_oauth.time, "time", return_value=1_000), patch.object(
            connect_oauth.secrets,
            "token_urlsafe",
            return_value="fixed-state-nonce-value",
        ):
            state, verifier = connect_oauth.build_signed_state(
                "google", "hint@gmail.com", "owner@example.com", "stable-secret"
            )
        with patch.object(oauth_callback.time, "time", return_value=1_001):
            payload, error = oauth_callback.verify_signed_state(state, "stable-secret")
        self.assertIsNone(error)
        self.assertEqual(payload["code_verifier"], verifier)
        self.assertTrue(
            oauth_callback.verify_owner_binding(
                payload, " OWNER@EXAMPLE.COM ", "stable-secret"
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
                "google", "hint@gmail.com", "owner@example.com", "stable-secret"
            )
        self.assertEqual(separate_state, state)
        self.assertEqual(separate_verifier, verifier)

    def test_beta_cookie_is_redirect_compatible_and_callback_requires_it(self):
        cookie = beta_auth.build_beta_session_cookie(
            "session-token",
            {"host": "app.example.com", "x-forwarded-proto": "https"},
        )
        self.assertIn("Path=/", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn("Domain=", cookie)

        state, _ = connect_oauth.build_signed_state(
            "google", "hint@gmail.com", "owner@example.com", "state-secret"
        )
        callback = Mock()
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state}"
        callback.headers = {"host": "app.example.com"}
        callback._send_callback_page = Mock()
        with patch.dict(
            oauth_callback.os.environ,
            {
                "CUEVION_OAUTH_STATE_SECRET": "state-secret",
                "CUEVION_BETA_SESSION_SECRET": "session-secret",
                "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
            },
            clear=False,
        ), patch.object(oauth_callback, "_exchange_google_code") as exchange:
            oauth_callback.handler.do_GET(callback)
        exchange.assert_not_called()
        self.assertFalse(callback._send_callback_page.call_args.args[0]["connected"])

    def test_callback_rejects_owner_mismatch_before_token_exchange(self):
        callback = Mock()
        state_value, _ = connect_oauth.build_signed_state(
            "google", "attacker@gmail.com", "owner@example.com", "state-secret"
        )
        with patch.dict(
            beta_auth.os.environ,
            {"CUEVION_BETA_SESSION_SECRET": "session-secret"},
            clear=False,
        ):
            session = beta_auth.build_beta_session_token(
                name="Other", email="other@example.com"
            )
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state_value}"
        callback.headers = {"cookie": f"cuevion_beta_session={session}"}
        callback._send_callback_page = Mock()
        with patch.dict(
            oauth_callback.os.environ,
            {
                "CUEVION_OAUTH_STATE_SECRET": "state-secret",
                "CUEVION_BETA_SESSION_SECRET": "session-secret",
            },
            clear=False,
        ), patch.object(oauth_callback, "_exchange_google_code") as exchange:
            oauth_callback.handler.do_GET(callback)
        exchange.assert_not_called()
        response = callback._send_callback_page.call_args.args[0]
        self.assertFalse(response["connected"])
        self.assertEqual(response["email"], "")

    def test_callback_uses_verified_email_and_persists_owner(self):
        callback = Mock()
        state_value, _ = connect_oauth.build_signed_state(
            "google", "attacker@gmail.com", "owner@example.com", "state-secret"
        )
        with patch.dict(
            beta_auth.os.environ,
            {"CUEVION_BETA_SESSION_SECRET": "session-secret"},
            clear=False,
        ):
            session = beta_auth.build_beta_session_token(
                name="Owner", email="owner@example.com"
            )
        callback.path = f"/api/inboxes/oauth-callback?code=code&state={state_value}"
        callback.headers = {"cookie": f"cuevion_beta_session={session}"}
        callback._send_callback_page = Mock()
        environment = {
            "GOOGLE_CLIENT_ID": "client",
            "GOOGLE_CLIENT_SECRET": "secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "CUEVION_BETA_SESSION_SECRET": "session-secret",
        }
        with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
            oauth_callback, "_exchange_google_code", return_value=({"access_token": "secret-token"}, None)
        ), patch.object(
            oauth_callback,
            "_fetch_verified_google_identity",
            return_value=({"email": "verified@gmail.com", "display_name": "Verified"}, None),
        ), patch.object(
            oauth_callback,
            "persist_google_token_record",
            return_value=({"_storage_durable": True}, None),
        ) as persist, patch.object(
            oauth_callback, "_upsert_gmail_managed_inbox_in_user_config", return_value=None
        ) as upsert:
            oauth_callback.handler.do_GET(callback)
        persist.assert_called_once_with(
            email="verified@gmail.com",
            owner_email="owner@example.com",
            token_payload={"access_token": "secret-token"},
        )
        self.assertEqual(upsert.call_args.kwargs["email"], "verified@gmail.com")
        self.assertEqual(upsert.call_args.kwargs["owner_email"], "owner@example.com")
        response = callback._send_callback_page.call_args.args[0]
        self.assertEqual(response["email"], "verified@gmail.com")
        self.assertNotIn("attacker@gmail.com", json.dumps(response))

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

    def test_config_write_requires_ok_and_exact_readback(self):
        environment = {
            "CUEVION_BETA_SESSION_SECRET": "session-secret",
            "KV_REST_API_URL": "https://kv.example",
            "KV_REST_API_TOKEN": "kv-secret",
        }
        saved_record = None
        calls = []

        def successful_transport(request, timeout):
            nonlocal saved_record
            calls.append(request.get_method())
            if request.get_method() == "POST":
                saved_record = json.loads(request.data)
                return BoundaryResponse('{"result":"OK"}')
            if saved_record is None:
                return BoundaryResponse('{"result":null}')
            return BoundaryResponse(json.dumps({"result": json.dumps(saved_record)}))

        with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
            oauth_callback,
            "urlopen",
            side_effect=successful_transport,
        ):
            error = oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                self._authenticated_headers(),
                email="verified@gmail.com",
                display_name="Verified",
                owner_email="owner@example.com",
                message="Connected",
            )
        self.assertIsNone(error)
        self.assertEqual(calls, ["GET", "POST", "GET"])

        failed_acknowledgements = [b"", b"{", b'{"result":null}', b'{"result":"STALE"}']
        for acknowledgement in failed_acknowledgements:
            def failed_ack_transport(request, timeout, ack=acknowledgement):
                if request.get_method() == "GET":
                    return BoundaryResponse('{"result":null}')
                return BoundaryResponse(ack)

            with self.subTest(acknowledgement=acknowledgement), patch.dict(
                oauth_callback.os.environ, environment, clear=False
            ), patch.object(
                oauth_callback,
                "urlopen",
                side_effect=failed_ack_transport,
            ):
                error = oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                    self._authenticated_headers(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                )
            self.assertEqual(error["code"], "user_config_persistence_failed")

    def test_config_readback_rejects_missing_stale_email_owner_and_oversize(self):
        environment = {
            "CUEVION_BETA_SESSION_SECRET": "session-secret",
            "KV_REST_API_URL": "https://kv.example",
            "KV_REST_API_TOKEN": "kv-secret",
        }

        def run_with_mutation(mutation):
            saved_record = None

            def transport(request, timeout):
                nonlocal saved_record
                if request.get_method() == "POST":
                    saved_record = json.loads(request.data)
                    return BoundaryResponse('{"result":"OK"}')
                if saved_record is None:
                    return BoundaryResponse('{"result":null}')
                return mutation(saved_record)

            with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
                oauth_callback,
                "urlopen",
                side_effect=transport,
            ):
                return oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                    self._authenticated_headers(),
                    email="verified@gmail.com",
                    display_name="Verified",
                    owner_email="owner@example.com",
                    message="Connected",
                )

        def altered(field, value):
            def mutate(record):
                copy = json.loads(json.dumps(record))
                copy["managedInboxes"][0][field] = value
                return BoundaryResponse(json.dumps({"result": json.dumps(copy)}))
            return mutate

        mutations = [
            lambda record: BoundaryResponse('{"result":null}'),
            lambda record: BoundaryResponse(json.dumps({"result": json.dumps({**record, "managedInboxes": []})})),
            lambda record: BoundaryResponse(
                json.dumps(
                    {
                        "result": json.dumps(
                            {**record, "updatedAt": "2000-01-01T00:00:00Z"}
                        )
                    }
                )
            ),
            altered("email", "other@gmail.com"),
            altered("oauthOwnerEmail", "other@example.com"),
            lambda record: BoundaryResponse(b"x" * (oauth_callback.MAX_OAUTH_RESPONSE_BYTES + 1)),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                error = run_with_mutation(mutation)
            self.assertEqual(error["code"], "user_config_persistence_failed")

        with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
            oauth_callback,
            "urlopen",
            side_effect=TimeoutError("raw timeout detail"),
        ):
            error = oauth_callback._upsert_gmail_managed_inbox_in_user_config(
                self._authenticated_headers(),
                email="verified@gmail.com",
                display_name="Verified",
                owner_email="owner@example.com",
                message="Connected",
            )
        self.assertEqual(error["code"], "user_config_persistence_failed")
        self.assertNotIn("raw timeout", json.dumps(error))

    def test_real_callback_transport_chain_requires_config_persistence_before_success(self):
        environment = {
            "CUEVION_BETA_SESSION_SECRET": "session-secret",
            "CUEVION_OAUTH_STATE_SECRET": "state-secret",
            "GOOGLE_CLIENT_ID": "client-id",
            "GOOGLE_CLIENT_SECRET": "client-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://app.example.com/api/inboxes/oauth-callback",
            "KV_REST_API_URL": "https://kv.example",
            "KV_REST_API_TOKEN": "kv-secret",
        }

        def run_callback(*, config_ack="OK"):
            state, _ = connect_oauth.build_signed_state(
                "google", "hint@gmail.com", "owner@example.com", "state-secret"
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
                    if request.get_method() == "POST":
                        token_record = json.loads(request.data)
                        return BoundaryResponse('{"result":"OK"}')
                    return BoundaryResponse(
                        json.dumps(
                            {"result": json.dumps(token_record) if token_record else None}
                        )
                    )
                if "cuevion%3Auser%3Av1" in url:
                    if request.get_method() == "POST":
                        config_record = json.loads(request.data)
                        return BoundaryResponse(json.dumps({"result": config_ack}))
                    return BoundaryResponse(
                        json.dumps(
                            {"result": json.dumps(config_record) if config_record else None}
                        )
                    )
                raise AssertionError(f"Unexpected mocked transport URL: {url}")

            with patch.dict(oauth_callback.os.environ, environment, clear=False), patch.object(
                oauth_callback,
                "urlopen",
                side_effect=transport,
            ):
                oauth_callback.handler.do_GET(callback)
            return callback._send_callback_page.call_args.args[0], token_record, config_record

        success, token_record, config_record = run_callback()
        self.assertTrue(success["connected"])
        self.assertEqual(token_record["owner_email"], "owner@example.com")
        self.assertEqual(config_record["managedInboxes"][0]["email"], "verified@gmail.com")
        self.assertEqual(
            config_record["managedInboxes"][0]["oauthOwnerEmail"],
            "owner@example.com",
        )

        failure, _, _ = run_callback(config_ack="STALE")
        self.assertFalse(failure["connected"])
        self.assertEqual(failure["connectionStatus"], "authenticated_pending_activation")
        self.assertNotIn("STALE", json.dumps(failure))
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
        self.assertEqual(merged_custom["managedInboxes"], custom["managedInboxes"])

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
            {"id": "gmail-1", "title": "Duplicate two"},
            {"id": "GMAIL-1", "title": "Duplicate three"},
        ]
        merged = config_route._merge_server_owned_google_inboxes(
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

        custom = {"id": "imap-1", "provider": "custom_imap", "custom": {"x": 1}}
        custom_result = config_route._merge_server_owned_google_inboxes(
            [existing_google], [custom]
        )
        self.assertEqual(custom_result[0], custom)
        self.assertEqual(custom_result[1], existing_google)

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
            {"title": "x" * (config_route.MAX_GOOGLE_INBOX_TITLE_LENGTH + 1)},
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
                merged = config_route._merge_server_owned_google_inboxes(
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
                merged = config_route._merge_server_owned_google_inboxes(
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
            config_route._merge_server_owned_google_inboxes([existing_google], [custom])[0],
            custom,
        )

    def test_imports_are_side_effect_free(self):
        with patch.object(authenticated_gmail, "resolve_owned_managed_inbox_record") as ownership, patch.object(
            authenticated_gmail, "load_google_token_record_with_metadata"
        ) as token_load, patch.object(authenticated_gmail, "refresh_google_token_record") as refresh:
            load_route("authenticated_gmail.py", "authenticated_gmail_import_safety_test")
        ownership.assert_not_called()
        token_load.assert_not_called()
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
