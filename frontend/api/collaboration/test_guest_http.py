from __future__ import annotations

import base64
import io
import json
import unittest
from unittest import mock

from . import guest_http, guest_rate_limit, guest_session, http_adapter, mutations


NOW = 1_800_000_000
ORIGIN = "https://app.cuevion.com"
INVITE_TOKEN = base64.urlsafe_b64encode(b"i" * 32).rstrip(b"=").decode("ascii")
SESSION_ID = base64.urlsafe_b64encode(b"s" * 32).rstrip(b"=").decode("ascii")
CSRF_TOKEN = base64.urlsafe_b64encode(b"c" * 32).rstrip(b"=").decode("ascii")
COLLABORATION_ID = "C" * 22
GUEST_KEY = b"guest-csrf-key-material-32-bytes!"
OTHER_GUEST_KEY = b"other-guest-key-material-32-byte"
OWNER_KEY = b"owner-csrf-key-material-32-bytes!"
RATE_KEY = b"guest-rate-limit-material-32-byte"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _environment(**updates: str) -> dict[str, str]:
    result = {
        guest_http.GUEST_HTTP_MODE_ENVIRONMENT_NAME: guest_http.GUEST_HTTP_MODE_ACTIVE,
        "VERCEL_ENV": "production",
        "CUEVION_APP_ORIGIN": ORIGIN,
        guest_session.GUEST_CSRF_HMAC_ENV: _b64(GUEST_KEY),
        "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(OWNER_KEY),
        guest_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY),
    }
    result.update(updates)
    return result


class _Headers:
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def raw_items(self):
        return iter(self._pairs)


class _GuardedReader:
    def read(self, _size: int) -> bytes:
        raise AssertionError("body must not be read")


class _Request:
    def __init__(
        self,
        body: bytes = b"",
        *,
        method: str = "POST",
        path: str = guest_http.GUEST_ENDPOINT_PATH,
        headers: list[tuple[str, str]] | None = None,
        guarded: bool = False,
    ) -> None:
        self.command = method
        self.path = path
        self.headers = _Headers(headers or [])
        self.rfile = _GuardedReader() if guarded else io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        return


def _post(
    payload: object,
    *,
    cookie: str | None = None,
    csrf: str | None = None,
    origin: str | None = ORIGIN,
    content_type: str = "application/json",
    path: str = guest_http.GUEST_ENDPOINT_PATH,
    extra_headers: list[tuple[str, str]] | None = None,
) -> _Request:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers: list[tuple[str, str]] = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    if origin is not None:
        headers.insert(0, ("Origin", origin))
    if cookie is not None:
        headers.append(("Cookie", cookie))
    if csrf is not None:
        headers.append((guest_session.CSRF_HEADER_NAME, csrf))
    headers.extend(extra_headers or [])
    return _Request(body, path=path, headers=headers)


def _get(
    *,
    cookie: str | None = None,
    path: str = guest_http.GUEST_ENDPOINT_PATH,
    content_length: str | None = "0",
    extra_headers: list[tuple[str, str]] | None = None,
) -> _Request:
    headers: list[tuple[str, str]] = []
    if content_length is not None:
        headers.append(("Content-Length", content_length))
    if cookie is not None:
        headers.append(("Cookie", cookie))
    headers.extend(extra_headers or [])
    return _Request(b"", method="GET", path=path, headers=headers)


def _cookie(session_id: str = SESSION_ID) -> str:
    return f"{guest_session.GUEST_SESSION_COOKIE_NAME}={session_id}"


def _session() -> dict:
    return {
        "collaborationId": COLLABORATION_ID,
        "guestDisplayName": "Guest Person",
        "allowedActions": ["read", "reply"],
        "identityAssurance": "link_possession",
        "expiresAt": NOW + 3600,
    }


def _collaboration() -> dict:
    return {
        "collaborationId": COLLABORATION_ID,
        "state": "needs_review",
        "updatedAt": NOW * 1000,
        "allowedActions": ["read", "reply"],
        "sharedSource": {
            "subject": "Subject",
            "senderDisplay": "Sender",
            "fromDisplay": "sender@example.com",
            "timestamp": "2027-01-15T08:00:00Z",
            "bodyText": "Shared source",
        },
        "messages": [
            {
                "id": "M" * 22,
                "authorDisplayName": "Guest Person",
                "authorRole": "Guest reviewer",
                "text": "Shared reply",
                "timestamp": NOW * 1000,
            }
        ],
    }


def _json(response: http_adapter.PublicResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _invoke(
    request: _Request,
    *,
    mode: str = guest_http.GUEST_HTTP_MODE_ACTIVE,
    environment: dict[str, str] | None = None,
) -> http_adapter.PublicResponse:
    return http_adapter.invoke_safely(
        lambda: guest_http.guest_response(
            request,
            http_mode=mode,
            environment=_environment() if environment is None else environment,
            now=NOW,
        ),
        allow_method="GET, POST",
    )


class GuestHttpBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rate_patch = mock.patch.object(
            guest_rate_limit,
            "consume_guest_rate_limit",
            return_value=guest_rate_limit.GuestRateLimitDecision("allowed"),
        )
        self.rate = self.rate_patch.start()
        self.addCleanup(self.rate_patch.stop)

    def assert_no_store(self, response: http_adapter.PublicResponse) -> None:
        self.assertIn(("Cache-Control", "no-store"), response.headers)
        self.assertFalse(
            any(name.lower().startswith("access-control-") for name, _ in response.headers)
        )

    def test_mode_is_exact_and_off_is_opaque(self):
        request = _Request(guarded=True)
        for mode in ("off", "", "guest", "owner_write", " guest_on "):
            with self.subTest(mode=mode):
                response = _invoke(request, mode=mode)
                self.assertEqual(response.status, 404)
                self.assertEqual(_json(response)["error"]["code"], "not_found")
                self.assert_no_store(response)
        self.rate.assert_not_called()

    def test_wrong_methods_are_rejected_with_canonical_allow(self):
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                response = _invoke(_Request(method=method, guarded=True))
                self.assertEqual(response.status, 405)
                self.assertEqual(response.headers[-1], ("Allow", "GET, POST"))

    def test_missing_or_malformed_security_configuration_fails_closed(self):
        for updates in (
            {guest_session.GUEST_CSRF_HMAC_ENV: ""},
            {"CUEVION_APP_ORIGIN": "https://app.cuevion.com/path"},
            {"CUEVION_APP_ORIGIN": ""},
            {guest_rate_limit.RATE_LIMIT_HMAC_ENV: "bad"},
            {guest_session.GUEST_CSRF_HMAC_ENV: _b64(RATE_KEY)},
        ):
            with self.subTest(updates=updates):
                response = _invoke(_post({"operation": "bootstrap"}), environment=_environment(**updates))
                self.assertEqual(response.status, 503)
                self.assertEqual(_json(response)["error"]["code"], "service_unavailable")

    def test_query_parameters_never_authenticate_or_exchange(self):
        with mock.patch.object(guest_session, "exchange_v2_invitation") as exchange:
            response = _invoke(
                _get(path=f"{guest_http.GUEST_ENDPOINT_PATH}?token={INVITE_TOKEN}")
            )
            self.assertEqual(response.status, 400)
            response = _invoke(
                _post(
                    {"operation": "exchange", "displayName": "Guest"},
                    path=f"{guest_http.GUEST_ENDPOINT_PATH}?token={INVITE_TOKEN}",
                )
            )
            self.assertEqual(response.status, 400)
        exchange.assert_not_called()

    def test_exchange_requires_origin_content_type_json_and_exact_fields(self):
        valid = {"operation": "exchange", "token": INVITE_TOKEN, "displayName": "Guest"}
        cases = (
            (_post(valid, origin=None), 403, "origin_rejected"),
            (_post(valid, origin="https://evil.example"), 403, "origin_rejected"),
            (_post(valid, content_type="text/plain"), 415, "unsupported_media_type"),
            (_Request(b"{", headers=[("Origin", ORIGIN), ("Content-Type", "application/json"), ("Content-Length", "1")]), 400, "invalid_request"),
            (_post({**valid, "collaborationId": COLLABORATION_ID}), 400, "invalid_request"),
            (_post({**valid, "token": "short"}), 400, "invalid_request"),
            (_post({**valid, "displayName": " Guest"}), 400, "invalid_request"),
        )
        with mock.patch.object(guest_session, "exchange_v2_invitation") as exchange:
            for request, status, code in cases:
                with self.subTest(status=status, code=code):
                    response = _invoke(request)
                    self.assertEqual(response.status, status)
                    self.assertEqual(_json(response)["error"]["code"], code)
                    self.assert_no_store(response)
        exchange.assert_not_called()

    def test_exchange_sets_http_only_cookie_and_projects_only_safe_values(self):
        def exchange(_token, **kwargs):
            csrf = kwargs["csrf_token_deriver"](SESSION_ID)
            return {
                "status": "ok",
                "sessionId": SESSION_ID,
                "csrfToken": csrf,
                "session": _session(),
                "error": None,
            }

        request = _post(
            {"operation": "exchange", "token": INVITE_TOKEN, "displayName": "Guest Person"}
        )
        with mock.patch.object(guest_session, "exchange_v2_invitation", side_effect=exchange):
            response = _invoke(request)
        self.assertEqual(response.status, 200)
        payload = _json(response)
        serialized = response.body.decode("utf-8")
        self.assertEqual(payload["data"]["session"], _session())
        self.assertTrue(guest_session.is_v2_guest_bearer(payload["data"]["csrfToken"]))
        self.assertNotIn(SESSION_ID, serialized)
        self.assertNotIn(INVITE_TOKEN, serialized)
        self.assertNotIn("sessionId", serialized)
        cookie = dict(response.headers)["Set-Cookie"]
        for attribute in (
            f"{guest_session.GUEST_SESSION_COOKIE_NAME}={SESSION_ID}",
            "Path=/api/collaboration/guest",
            "HttpOnly",
            "SameSite=Lax",
            "Secure",
        ):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assert_no_store(response)

    def test_exchange_lifecycle_errors_are_safe_and_distinct(self):
        expected = {
            "invite_not_found": (404, "invitation_invalid"),
            "invite_expired": (410, "invitation_expired"),
            "invite_revoked": (410, "invitation_revoked"),
            "invite_already_exchanged": (409, "invitation_already_exchanged"),
            "storage_unavailable": (503, "service_unavailable"),
            "secret-redis-detail": (500, "internal_error"),
        }
        request_payload = {"operation": "exchange", "token": INVITE_TOKEN, "displayName": "Guest"}
        for internal, public in expected.items():
            with self.subTest(code=internal), mock.patch.object(
                guest_session,
                "exchange_v2_invitation",
                return_value={"status": "error", "error": {"code": internal}},
            ):
                response = _invoke(_post(request_payload))
                self.assertEqual((response.status, _json(response)["error"]["code"]), public)
                self.assertNotIn(internal, response.body.decode("utf-8"))

    def test_exchange_rate_limit_precedes_exchange_storage(self):
        self.rate.return_value = guest_rate_limit.GuestRateLimitDecision("limited", 17)
        with mock.patch.object(guest_session, "exchange_v2_invitation") as exchange:
            response = _invoke(
                _post({"operation": "exchange", "token": INVITE_TOKEN, "displayName": "Guest"})
            )
        self.assertEqual(response.status, 429)
        self.assertEqual(dict(response.headers)["Retry-After"], "17")
        exchange.assert_not_called()

    def test_bootstrap_requires_exact_origin_and_cookie(self):
        cases = (
            (_post({"operation": "bootstrap"}, origin=None), 403, "origin_rejected"),
            (_post({"operation": "bootstrap"}), 401, "session_missing"),
            (_post({"operation": "bootstrap"}, cookie="other=value"), 401, "session_missing"),
            (_post({"operation": "bootstrap"}, cookie=f"{_cookie()}; {_cookie()}"), 400, "invalid_request"),
            (_post({"operation": "bootstrap", "text": "extra"}, cookie=_cookie()), 400, "invalid_request"),
        )
        with mock.patch.object(guest_session, "bootstrap_v2_guest_session") as bootstrap:
            for request, status, code in cases:
                with self.subTest(status=status, code=code):
                    response = _invoke(request)
                    self.assertEqual(response.status, status)
                    self.assertEqual(_json(response)["error"]["code"], code)
        bootstrap.assert_not_called()

    def test_bootstrap_recovers_stable_csrf_without_rotation(self):
        configuration = guest_session.parse_guest_csrf_configuration(
            {guest_session.GUEST_CSRF_HMAC_ENV: _b64(GUEST_KEY)}
        )
        expected = guest_session.derive_guest_csrf_token(SESSION_ID, configuration)

        def bootstrap(session_id, **kwargs):
            return {
                "status": "ok",
                "session": _session(),
                "csrfToken": kwargs["csrf_token_deriver"](session_id),
                "error": None,
            }

        request = _post({"operation": "bootstrap"}, cookie=_cookie())
        with mock.patch.object(guest_session, "bootstrap_v2_guest_session", side_effect=bootstrap):
            first = _invoke(request)
            second = _invoke(_post({"operation": "bootstrap"}, cookie=_cookie()))
        self.assertEqual(_json(first)["data"]["csrfToken"], expected)
        self.assertEqual(_json(second)["data"]["csrfToken"], expected)
        self.assertNotIn(SESSION_ID, first.body.decode("utf-8"))
        self.assertNotIn("Set-Cookie", dict(first.headers))

    def test_bootstrap_rate_limit_precedes_session_storage(self):
        self.rate.return_value = guest_rate_limit.GuestRateLimitDecision("limited", 3)
        with mock.patch.object(guest_session, "bootstrap_v2_guest_session") as bootstrap:
            response = _invoke(_post({"operation": "bootstrap"}, cookie=_cookie()))
        self.assertEqual(response.status, 429)
        bootstrap.assert_not_called()

    def test_read_needs_cookie_but_not_csrf_and_returns_guest_dto_only(self):
        with mock.patch.object(
            guest_http.application,
            "read_v2_collaboration_for_guest",
            return_value={"status": "ok", "collaboration": _collaboration(), "error": None},
        ) as read:
            response = _invoke(_get(cookie=_cookie()))
        self.assertEqual(response.status, 200)
        collaboration = _json(response)["data"]["collaboration"]
        self.assertEqual(collaboration, _collaboration())
        serialized = json.dumps(collaboration)
        for forbidden in (
            "participants", "externalGuests", "workspaceId", "mailboxId", "ownerEmail", "internal"
        ):
            self.assertNotIn(forbidden, serialized)
        read.assert_called_once()
        self.assert_no_store(response)

    def test_read_rejects_body_missing_cookie_and_rate_limit_before_storage(self):
        with mock.patch.object(guest_http.application, "read_v2_collaboration_for_guest") as read:
            self.assertEqual(_invoke(_get(cookie=None)).status, 401)
            self.assertEqual(_invoke(_get(cookie=_cookie(), content_length="1")).status, 400)
            self.rate.return_value = guest_rate_limit.GuestRateLimitDecision("limited", 1)
            self.assertEqual(_invoke(_get(cookie=_cookie())).status, 429)
        read.assert_not_called()

    def test_reply_exact_fields_and_rate_limit_precede_mutation(self):
        base = _post({"operation": "reply", "text": "Hello"}, cookie=_cookie(), csrf=CSRF_TOKEN)
        forbidden_payloads = (
            {"operation": "reply", "text": "Hello", "collaborationId": COLLABORATION_ID},
            {"operation": "reply", "text": "Hello", "displayName": "Spoof"},
            {"operation": "reply", "text": "Hello", "visibility": "internal"},
            {"operation": "reply", "text": "Hello", "ownerEmail": "owner@example.com"},
        )
        with mock.patch.object(guest_http.application, "append_v2_shared_reply_for_guest") as append:
            for payload in forbidden_payloads:
                self.assertEqual(
                    _invoke(_post(payload, cookie=_cookie(), csrf=CSRF_TOKEN)).status,
                    400,
                )
            self.rate.return_value = guest_rate_limit.GuestRateLimitDecision("limited", 9)
            self.assertEqual(_invoke(base).status, 429)
        append.assert_not_called()

    def test_reply_returns_authoritative_shared_only_state(self):
        with mock.patch.object(
            guest_http.application,
            "append_v2_shared_reply_for_guest",
            return_value={"status": "ok", "collaboration": _collaboration(), "error": None},
        ) as append:
            response = _invoke(
                _post({"operation": "reply", "text": "Hello"}, cookie=_cookie(), csrf=CSRF_TOKEN)
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(_json(response)["data"]["collaboration"], _collaboration())
        args, _kwargs = append.call_args
        self.assertEqual(args[1], "Hello")

    def test_reply_maps_csrf_and_session_failures_without_details(self):
        for code, expected in (
            ("csrf_failed", (403, "csrf_failed")),
            ("session_expired", (401, "session_expired")),
            ("session_revoked", (401, "session_revoked")),
        ):
            with self.subTest(code=code), mock.patch.object(
                guest_http.application,
                "append_v2_shared_reply_for_guest",
                return_value={"status": "error", "error": {"code": code}},
            ):
                response = _invoke(
                    _post({"operation": "reply", "text": "Hello"}, cookie=_cookie(), csrf=CSRF_TOKEN)
                )
                self.assertEqual((response.status, _json(response)["error"]["code"]), expected)

    def test_reply_and_logout_require_csrf(self):
        for operation in (
            {"operation": "reply", "text": "Hello"},
            {"operation": "logout"},
        ):
            with self.subTest(operation=operation["operation"]):
                response = _invoke(_post(operation, cookie=_cookie()))
                self.assertEqual(response.status, 403)
                self.assertEqual(_json(response)["error"]["code"], "csrf_failed")
                self.assertNotIn("Set-Cookie", dict(response.headers))

    def test_logout_resolves_capability_invalidates_and_clears_cookie(self):
        capability = object()
        with mock.patch.object(
            guest_session,
            "resolve_guest_v2_mutation_context",
            return_value={"status": "ok", "context": capability, "error": None},
        ) as resolve, mock.patch.object(
            guest_session,
            "logout_v2_guest_session",
            return_value={"status": "ok", "error": None},
        ) as logout:
            response = _invoke(
                _post({"operation": "logout"}, cookie=_cookie(), csrf=CSRF_TOKEN)
            )
        self.assertEqual(response.status, 200)
        cookie = dict(response.headers)["Set-Cookie"]
        for value in (
            f"{guest_session.GUEST_SESSION_COOKIE_NAME}=",
            "Path=/api/collaboration/guest",
            "Max-Age=0",
            "HttpOnly",
            "SameSite=Lax",
            "Secure",
        ):
            self.assertIn(value, cookie)
        resolve.assert_called_once()
        logout.assert_called_once_with(capability, now=NOW, command_transport=None)

    def test_logout_never_clears_cookie_before_valid_same_origin_security(self):
        requests = (
            _post({"operation": "logout"}, cookie=_cookie(), csrf=CSRF_TOKEN, origin=None),
            _post({"operation": "logout"}, cookie=_cookie()),
            _post({"operation": "logout", "text": "extra"}, cookie=_cookie(), csrf=CSRF_TOKEN),
        )
        for request in requests:
            response = _invoke(request)
            self.assertNotIn("Set-Cookie", dict(response.headers))

    def test_logout_rate_limit_precedes_resolution_and_mutation(self):
        self.rate.return_value = guest_rate_limit.GuestRateLimitDecision("limited", 5)
        with mock.patch.object(guest_session, "resolve_guest_v2_mutation_context") as resolve, mock.patch.object(
            guest_session, "logout_v2_guest_session"
        ) as logout:
            response = _invoke(
                _post({"operation": "logout"}, cookie=_cookie(), csrf=CSRF_TOKEN)
            )
        self.assertEqual(response.status, 429)
        resolve.assert_not_called()
        logout.assert_not_called()

    def test_oversized_body_is_rejected_before_read_or_mutation(self):
        request = _Request(
            guarded=True,
            headers=[
                ("Origin", ORIGIN),
                ("Content-Type", "application/json"),
                ("Content-Length", str(guest_http.MAX_GUEST_REQUEST_BYTES + 1)),
            ],
        )
        response = _invoke(request)
        self.assertEqual(response.status, 413)
        self.assertEqual(_json(response)["error"]["code"], "payload_too_large")

    def test_duplicate_and_comma_combined_security_headers_are_rejected(self):
        base = _post({"operation": "bootstrap"}, cookie=_cookie())
        for header in (
            ("Origin", ORIGIN),
            ("Cookie", _cookie()),
            ("X-Cuevion-CSRF", f"{CSRF_TOKEN},{CSRF_TOKEN}"),
        ):
            request = _post(
                {"operation": "bootstrap"},
                cookie=_cookie(),
                extra_headers=[header],
            )
            with self.subTest(header=header[0]):
                self.assertEqual(_invoke(request).status, 400)
        self.assertIsNotNone(base)


class GuestRouteActivationTests(unittest.TestCase):
    def test_activation_off_does_not_import_guest_services_or_read_body(self):
        from . import guest

        request = _Request(guarded=True)
        with mock.patch.dict(guest.os.environ, {}, clear=True), mock.patch.object(
            guest.importlib,
            "import_module",
            side_effect=AssertionError("disabled route must not import guest services"),
        ):
            guest.handler._respond(request)
        self.assertEqual(request.status, 404)
        self.assertEqual(json.loads(request.wfile.getvalue())["error"]["code"], "not_found")

    def test_active_route_rejects_unsupported_method_with_both_allowed_methods(self):
        from . import guest

        request = _Request(method="PUT", guarded=True)
        with mock.patch.dict(
            guest.os.environ,
            {guest_http.GUEST_HTTP_MODE_ENVIRONMENT_NAME: guest_http.GUEST_HTTP_MODE_ACTIVE},
            clear=True,
        ):
            guest.handler._respond(request)
        self.assertEqual(request.status, 405)
        self.assertIn(("Allow", "GET, POST"), request.response_headers)


class GuestCsrfDerivationTests(unittest.TestCase):
    def configuration(self, key: bytes = GUEST_KEY):
        return guest_session.parse_guest_csrf_configuration(
            {
                guest_session.GUEST_CSRF_HMAC_ENV: _b64(key),
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(OWNER_KEY),
                guest_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY),
            }
        )

    def test_derivation_is_stable_session_bound_key_bound_and_canonical(self):
        first = guest_session.derive_guest_csrf_token(SESSION_ID, self.configuration())
        again = guest_session.derive_guest_csrf_token(SESSION_ID, self.configuration())
        other_session = guest_session.derive_guest_csrf_token(INVITE_TOKEN, self.configuration())
        other_key = guest_session.derive_guest_csrf_token(
            SESSION_ID, self.configuration(OTHER_GUEST_KEY)
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, other_session)
        self.assertNotEqual(first, other_key)
        self.assertNotEqual(first, SESSION_ID)
        self.assertTrue(guest_session.is_v2_guest_bearer(first))

    def test_missing_malformed_and_reused_key_fail_closed_without_key_output(self):
        invalid = (
            {},
            {guest_session.GUEST_CSRF_HMAC_ENV: "short"},
            {
                guest_session.GUEST_CSRF_HMAC_ENV: _b64(GUEST_KEY),
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(GUEST_KEY),
            },
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid guest CSRF configuration") as caught:
                    guest_session.parse_guest_csrf_configuration(value)
                self.assertNotIn(_b64(GUEST_KEY), str(caught.exception))
        self.assertEqual(repr(self.configuration()), "<GuestCsrfConfiguration>")

    def test_exchange_persists_hash_of_publicly_derivable_token_only(self):
        csrf_configuration = self.configuration()
        invite = {
            "inviteId": "I" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "wsp_" + ("w" * 22),
            "mailboxId": "primary.mailbox",
            "collaborationId": COLLABORATION_ID,
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "identityAssurance": "link_possession",
            "createdAt": NOW - 10,
            "expiresAt": NOW + 3600,
            "status": "active",
            "exchangeCount": 0,
        }
        captured: dict = {}

        def atomic(**kwargs):
            captured.update(kwargs)
            return {"status": "ok"}

        with mock.patch.object(
            guest_session, "_load_v2_invite_by_token", return_value={"status": "ok", "record": invite}
        ), mock.patch.object(guest_session, "_atomic_exchange_v2_invite", side_effect=atomic):
            result = guest_session.exchange_v2_invitation(
                INVITE_TOKEN,
                guest_display_name="Guest Person",
                now=NOW,
                csrf_token_deriver=lambda session_id: guest_session.derive_guest_csrf_token(
                    session_id, csrf_configuration
                ),
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            captured["session_record"]["csrfTokenHash"],
            guest_session.hash_v2_secret(result["csrfToken"]),
        )
        self.assertNotIn(result["csrfToken"], json.dumps(captured["session_record"]))
        self.assertNotIn(result["sessionId"], json.dumps(captured["session_record"]))

    def test_bootstrap_rederives_same_token_and_revalidates_stored_hash_and_graph(self):
        configuration = self.configuration()
        csrf_token = guest_session.derive_guest_csrf_token(SESSION_ID, configuration)
        session = {
            "v": 2,
            "sessionHash": guest_session.hash_v2_secret(SESSION_ID),
            "inviteId": "I" * 22,
            "ownerEmail": "owner@example.com",
            "workspaceId": "wsp_" + ("w" * 22),
            "mailboxId": "primary.mailbox",
            "collaborationId": COLLABORATION_ID,
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "identityAssurance": "link_possession",
            "guestDisplayName": "Guest Person",
            "createdAt": NOW - 60,
            "lastUsedAt": NOW - 60,
            "expiresAt": NOW + 3600,
            "status": "active",
            "csrfTokenHash": guest_session.hash_v2_secret(csrf_token),
            "revokedAt": None,
            "loggedOutAt": None,
        }
        invite = {
            "status": "exchanged",
            "inviteId": session["inviteId"],
            "ownerEmail": session["ownerEmail"],
            "workspaceId": session["workspaceId"],
            "mailboxId": session["mailboxId"],
            "collaborationId": session["collaborationId"],
            "activeSessionHash": session["sessionHash"],
            "exchangedAt": session["createdAt"],
            "allowedActions": ["read", "reply"],
            "visibility": "shared_only",
            "expiresAt": NOW + 7200,
        }
        thread = {
            "ownerEmail": session["ownerEmail"],
            "workspaceId": session["workspaceId"],
            "mailboxId": session["mailboxId"],
            "collaborationId": session["collaborationId"],
        }
        deriver = lambda session_id: guest_session.derive_guest_csrf_token(
            session_id, configuration
        )
        with mock.patch.object(
            guest_session,
            "_load_v2_guest_session_record",
            return_value={"status": "ok", "record": session},
        ) as load_session, mock.patch.object(
            guest_session,
            "_load_v2_invite_by_id",
            return_value={"status": "ok", "record": invite},
        ) as load_invite, mock.patch.object(
            guest_session,
            "_load_v2_thread",
            side_effect=[
                {"status": "ok", "record": thread},
                {"status": "ok", "record": thread},
                {
                    "status": "ok",
                    "record": {**thread, "workspaceId": "wsp_" + ("x" * 22)},
                },
            ],
        ) as load_thread, mock.patch.object(
            guest_session,
            "normalize_v2_thread_record",
            side_effect=lambda value: value,
        ):
            first = guest_session.bootstrap_v2_guest_session(
                SESSION_ID, csrf_token_deriver=deriver, now=NOW
            )
            second = guest_session.bootstrap_v2_guest_session(
                SESSION_ID, csrf_token_deriver=deriver, now=NOW
            )
            mismatched = guest_session.bootstrap_v2_guest_session(
                SESSION_ID,
                csrf_token_deriver=lambda _session_id: CSRF_TOKEN,
                now=NOW,
            )
            invalid_thread = guest_session.bootstrap_v2_guest_session(
                SESSION_ID,
                csrf_token_deriver=deriver,
                now=NOW,
            )
        self.assertEqual(first["csrfToken"], second["csrfToken"])
        self.assertEqual(first["csrfToken"], csrf_token)
        self.assertEqual(mismatched["error"]["code"], "session_revoked")
        self.assertEqual(invalid_thread["error"]["code"], "session_revoked")
        self.assertEqual(load_session.call_count, 4)
        self.assertEqual(load_invite.call_count, 4)
        self.assertEqual(load_thread.call_count, 3)


class GuestReplyApplicationTests(unittest.TestCase):
    def test_wrapper_obtains_session_capability_and_accepts_only_text(self):
        capability = guest_session._GuestMutationCapability(
            guest_session._GUEST_MUTATION_SENTINEL,
            "a" * 64,
            "I" * 22,
            "owner@example.com",
            "wsp_" + ("w" * 22),
            "primary.mailbox",
            COLLABORATION_ID,
            "Trusted Guest",
            NOW + 3600,
            NOW - 60,
            NOW - 1,
        )
        thread = {
            "collaborationId": COLLABORATION_ID,
            "ownerEmail": capability.owner_email,
            "workspaceId": capability.workspace_id,
            "mailboxId": capability.mailbox_id,
        }
        resolved = {"status": "ok", "context": capability, "error": None}
        with mock.patch.object(
            guest_http.application,
            "resolve_guest_v2_mutation_context",
            return_value=resolved,
        ) as resolve, mock.patch.object(
            mutations,
            "append_guest_v2_reply",
            return_value={
                "status": "ok",
                "message": {
                    "id": "M" * 22,
                    "authorDisplayName": "Trusted Guest",
                    "authorRole": "Guest reviewer",
                    "text": "Only text",
                    "timestamp": NOW * 1000,
                    "visibility": "shared",
                },
                "updatedAt": NOW * 1000,
                "error": None,
            },
        ) as append, mock.patch.object(
            guest_http.application,
            "_load_exact_thread",
            return_value=(
                {
                    **thread,
                    "updatedAt": NOW * 1000,
                    "messages": [
                        {
                            "id": "M" * 22,
                            "authorKind": "guest",
                            "authorDisplayName": "Trusted Guest",
                            "text": "Only text",
                            "visibility": "shared",
                        }
                    ],
                },
                None,
            ),
        ), mock.patch.object(
            guest_http.application,
            "build_v2_guest_thread_dto",
            return_value=_collaboration(),
        ):
            result = guest_http.application.append_v2_shared_reply_for_guest(
                (("Origin", ORIGIN),),
                "Only text",
                now=NOW,
                environment=_environment(),
            )
        self.assertEqual(result["collaboration"], _collaboration())
        resolve.assert_called_once()
        append.assert_called_once_with(
            capability,
            "Only text",
            command_transport=None,
        )
        parameters = guest_http.application.append_v2_shared_reply_for_guest.__annotations__
        self.assertNotIn("collaborationId", parameters)
        self.assertNotIn("mailboxId", parameters)
        self.assertNotIn("workspaceId", parameters)


class GuestRateLimitTests(unittest.TestCase):
    def configuration(self):
        return guest_rate_limit.parse_guest_rate_limit_configuration(
            {
                guest_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY),
                guest_session.GUEST_CSRF_HMAC_ENV: _b64(GUEST_KEY),
                "CUEVION_COLLAB_V2_OWNER_CSRF_KEY": _b64(OWNER_KEY),
            }
        )

    def test_exact_private_beta_limits(self):
        expected = {
            guest_rate_limit.RATE_LIMIT_EXCHANGE: (120, 6),
            guest_rate_limit.RATE_LIMIT_BOOTSTRAP: (600, 12),
            guest_rate_limit.RATE_LIMIT_READ: (3000, 240),
            guest_rate_limit.RATE_LIMIT_REPLY: (600, 30),
            guest_rate_limit.RATE_LIMIT_LOGOUT: (600, 12),
        }
        for name, limits in expected.items():
            policy = guest_rate_limit.guest_rate_limit_policy(name)
            self.assertIsNotNone(policy)
            self.assertEqual((policy.global_limit, policy.scoped_limit), limits)
        self.assertEqual(guest_rate_limit.WINDOW_SECONDS, 60)

    def test_keys_hide_bearer_and_global_bucket_survives_token_changes(self):
        first = guest_rate_limit.build_guest_rate_limit_keys(
            SESSION_ID, guest_rate_limit.RATE_LIMIT_EXCHANGE, self.configuration()
        )
        second = guest_rate_limit.build_guest_rate_limit_keys(
            INVITE_TOKEN, guest_rate_limit.RATE_LIMIT_EXCHANGE, self.configuration()
        )
        self.assertIsNotNone(first)
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])
        for key in (*first, *second):
            self.assertNotIn(SESSION_ID, key)
            self.assertNotIn(INVITE_TOKEN, key)

    def test_configuration_rejects_missing_malformed_and_reused_secrets(self):
        for value in (
            {},
            {guest_rate_limit.RATE_LIMIT_HMAC_ENV: "bad"},
            {
                guest_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY),
                guest_session.GUEST_CSRF_HMAC_ENV: _b64(RATE_KEY),
            },
        ):
            with self.assertRaises(ValueError):
                guest_rate_limit.parse_guest_rate_limit_configuration(value)

    def test_consumer_maps_allowed_limited_and_protocol_failure(self):
        outcomes = (
            ({"status": "allowed"}, ("allowed", None)),
            ({"status": "limited", "retryAfter": "60"}, ("limited", 60)),
            ({"status": "limited", "retryAfter": "61"}, ("unavailable", None)),
            ({"status": "malformed"}, ("unavailable", None)),
            ({"status": "unexpected", "detail": SESSION_ID}, ("unavailable", None)),
        )
        for result, expected in outcomes:
            with self.subTest(result=result), mock.patch.object(
                guest_rate_limit, "_v2_eval", return_value=result
            ) as evaluate:
                decision = guest_rate_limit.consume_guest_rate_limit(
                    SESSION_ID,
                    guest_rate_limit.RATE_LIMIT_READ,
                    self.configuration(),
                )
                self.assertEqual((decision.status, decision.retry_after_seconds), expected)
                command = evaluate.call_args.args[0]
                self.assertNotIn(SESSION_ID, json.dumps(command))
                self.assertNotIn("SCAN", command)


if __name__ == "__main__":
    unittest.main()
