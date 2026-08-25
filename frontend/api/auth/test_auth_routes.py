"""Offline route-composition tests for the parallel Auth0 lane."""

from __future__ import annotations

import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from api.auth import (
    account_authority,
    auth0_flow,
    email_address,
    models,
    runtime,
    session_store,
)
from api.auth.callback import handler as CallbackHandler
from api.auth.test_account_authority import (
    EMAIL,
    ISSUER,
    SUBJECT,
    USER_ID,
    WORKSPACE_ID,
    _authority_result,
)
from cuevion_auth import current_account_repository_contract as contract


NOW = 1_800_000_000
ENVIRONMENT = {
    "CUEVION_AUTH0_DOMAIN": auth0_flow.AUTH0_DOMAIN,
    "CUEVION_AUTH0_CLIENT_ID": "route-test-client-id",
    "CUEVION_AUTH0_CLIENT_SECRET": "route-test-client-secret",
    "CUEVION_AUTH_SESSION_SECRET": "R" * 48,
}


def _encoded(byte: int, length: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * length).rstrip(b"=").decode("ascii")


def _header(response, name: str) -> list[str]:
    return [value for key, value in response.headers if key.casefold() == name.casefold()]


def _json(response):
    return json.loads(response.body.decode("utf-8"))


class FixedTransactionRandom:
    def __init__(self):
        self.values = iter((b"S" * 32, b"N" * 32, b"V" * 32, b"I" * 12))

    def __call__(self, length: int) -> bytes:
        value = next(self.values)
        if len(value) != length:
            raise AssertionError(length)
        return value


class FixedSessionRandom:
    def __init__(self):
        self.values = iter((b"C" * 32, b"D" * 32))

    def __call__(self, length: int) -> bytes:
        value = next(self.values)
        if len(value) != length:
            raise AssertionError(length)
        return value


class NeutralEmailHelperTests(unittest.TestCase):
    def test_established_normalization_and_validation_contract_is_preserved(self):
        self.assertEqual(
            email_address.normalize_auth_email("  USER@Example.COM  "),
            "user@example.com",
        )
        self.assertTrue(email_address.is_valid_auth_email(" user+tag@example.com "))
        for invalid in ("", "user@example", "user @example.com", "user@localhost"):
            with self.subTest(invalid=invalid):
                self.assertFalse(email_address.is_valid_auth_email(invalid))


class MemoryCommands:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        key = str(command[1])
        if operation == "SET":
            if command[-1] == "NX" and key in self.values:
                return {"result": None}
            self.values[key] = str(command[2])
            return {"result": "OK"}
        if operation == "GET":
            return {"result": self.values.get(key)}
        if operation == "DEL":
            existed = key in self.values
            self.values.pop(key, None)
            return {"result": 1 if existed else 0}
        raise AssertionError(operation)


class FakeAuthority:
    def __init__(self, identity_result=None, user_result=None):
        self.identity_result = identity_result
        self.user_result = user_result
        self.identity_calls = []
        self.user_calls = []

    def resolve_current_account_by_identity(self, key):
        self.identity_calls.append(key)
        return self.identity_result

    def read_current_account_by_user(self, user_id, workspace_id):
        self.user_calls.append((user_id, workspace_id))
        return self.user_result


class AdapterHeaders:
    def __init__(self, pairs):
        self.pairs = list(pairs)

    def raw_items(self):
        return list(self.pairs)


class AdapterHandler:
    def __init__(self, method: str, path: str, pairs):
        self.command = method
        self.path = path
        self.headers = AdapterHeaders(pairs)
        self.status = None
        self.response_headers = []
        self.wfile = io.BytesIO()

    def send_response_only(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        return None


def _user_result(
    outcome: contract.CurrentAccountReadOutcome = contract.CurrentAccountReadOutcome.FOUND,
):
    if outcome is not contract.CurrentAccountReadOutcome.FOUND:
        return contract.CurrentAccountByUserAuthorityResult(outcome, None)
    authority = _authority_result().authority
    return contract.CurrentAccountByUserAuthorityResult(
        outcome,
        contract.CurrentAccountByUserAuthority(
            authority.user,
            authority.primary_verified_email,
            authority.workspace,
            authority.workspace_membership,
        ),
    )


def _transaction_request():
    configuration = auth0_flow.parse_auth0_configuration(ENVIRONMENT)
    return auth0_flow.build_authorization_request(
        configuration, NOW, random_bytes=FixedTransactionRandom()
    )


def _transaction_headers(request, *, session_cookie: str | None = None):
    transaction = request.transaction_cookie.split(";", 1)[0]
    cookie = transaction if session_cookie is None else f"{transaction}; {session_cookie}"
    return (("host", "app.cuevion.com"), ("cookie", cookie))


def _validated_identity(*, email: str = EMAIL):
    return SimpleNamespace(
        issuer=ISSUER,
        subject=SUBJECT,
        email=email,
        issued_at=NOW - 10,
        expires_at=NOW + 3_600,
    )


class LoginAndCallbackTests(unittest.TestCase):
    def test_login_requires_get_and_canonical_host(self):
        response = runtime.login_response(
            "POST",
            (("host", "app.cuevion.com"),),
            environment=ENVIRONMENT,
            now=NOW,
        )
        self.assertEqual(response.status, 405)
        forbidden = runtime.login_response(
            "GET",
            (("host", "evil.example"),),
            environment=ENVIRONMENT,
            now=NOW,
        )
        self.assertEqual(forbidden.status, 403)

    def test_login_redirect_contains_protocol_values_and_no_secret(self):
        response = runtime.login_response(
            "GET",
            (("host", "app.cuevion.com"),),
            environment=ENVIRONMENT,
            now=NOW,
            random_bytes=FixedTransactionRandom(),
        )
        self.assertEqual(response.status, 303)
        location = _header(response, "location")[0]
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(query["connection"], ["email"])
        self.assertEqual(query["redirect_uri"], [auth0_flow.CALLBACK_URI])
        self.assertEqual(query["scope"], ["openid profile email"])
        self.assertNotIn(ENVIRONMENT["CUEVION_AUTH0_CLIENT_SECRET"], location)
        cookie = _header(response, "set-cookie")[0]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_callback_state_mismatch_clears_transaction_without_exchange(self):
        request = _transaction_request()
        commands = MemoryCommands()
        with mock.patch.object(
            runtime.auth0_flow, "exchange_authorization_code"
        ) as exchange:
            response = runtime.callback_response(
                "GET",
                _transaction_headers(request),
                "/api/auth/callback?code=auth-code&state=" + _encoded(9, 32),
                environment=ENVIRONMENT,
                now=NOW,
                session_store_factory=lambda _environment: session_store.AuthSessionStore(commands),
                authority_factory=lambda _environment: FakeAuthority(),
            )
        self.assertEqual(response.status, 303)
        self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
        self.assertTrue(any("Max-Age=0" in value for value in _header(response, "set-cookie")))
        self.assertEqual(commands.commands, [])
        exchange.assert_not_called()

    def _callback(
        self,
        *,
        authority_result=None,
        identity=None,
        commands=None,
        state=None,
    ):
        request = _transaction_request()
        selected_commands = MemoryCommands() if commands is None else commands
        selected_authority = (
            _authority_result() if authority_result is None else authority_result
        )
        authority = FakeAuthority(identity_result=selected_authority)
        returned_state = request.transaction.state if state is None else state
        with (
            mock.patch.object(
                runtime.auth0_flow,
                "exchange_authorization_code",
                return_value=SimpleNamespace(id_token="synthetic-id-token"),
            ),
            mock.patch.object(
                runtime.auth0_flow,
                "validate_id_token_with_jwks",
                return_value=_validated_identity() if identity is None else identity,
            ),
        ):
            response = runtime.callback_response(
                "GET",
                _transaction_headers(request),
                f"/api/auth/callback?code=auth-code&state={returned_state}",
                environment=ENVIRONMENT,
                now=NOW,
                token_transport=lambda _request: None,
                jwks_transport=lambda _request: None,
                session_store_factory=lambda _environment: session_store.AuthSessionStore(selected_commands),
                authority_factory=lambda _environment: authority,
                random_bytes=FixedSessionRandom(),
            )
        return response, selected_commands, authority

    def test_callback_authority_unavailable_fails_closed(self):
        unavailable = contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.UNAVAILABLE, None
        )
        response, commands, _authority = self._callback(authority_result=unavailable)
        self.assertEqual(response.status, 303)
        self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
        self.assertEqual([command[0] for command in commands.commands], ["SET"])
        self.assertTrue(str(commands.commands[0][1]).startswith(session_store.TRANSACTION_USE_KEY_PREFIX))

    def test_callback_stored_identity_mismatch_creates_no_session(self):
        response, commands, _authority = self._callback(
            identity=_validated_identity(email="different@example.com")
        )
        self.assertEqual(response.status, 303)
        self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
        self.assertEqual(len(commands.commands), 1)
        self.assertTrue(str(commands.commands[0][1]).startswith(session_store.TRANSACTION_USE_KEY_PREFIX))

    def test_successful_callback_creates_rotated_server_session(self):
        response, commands, authority = self._callback()
        self.assertEqual(response.status, 303)
        self.assertEqual(_header(response, "location"), ["/"])
        cookies = _header(response, "set-cookie")
        self.assertEqual(len(cookies), 2)
        self.assertTrue(any(value.startswith("__Host-cuevion_session=") for value in cookies))
        session_commands = [
            command
            for command in commands.commands
            if str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)
        ]
        self.assertEqual(len(session_commands), 1)
        self.assertNotIn("synthetic-id-token", str(session_commands[0]))
        self.assertEqual(len(authority.identity_calls), 1)

    def test_callback_post_clears_transaction_cookie_without_mutating_store(self):
        request = _transaction_request()
        commands = MemoryCommands()
        response = runtime.callback_response(
            "POST",
            _transaction_headers(request),
            "/api/auth/callback",
            environment=ENVIRONMENT,
            now=NOW,
            session_store_factory=lambda _environment: session_store.AuthSessionStore(commands),
        )
        self.assertEqual(response.status, 405)
        self.assertTrue(any("Max-Age=0" in value for value in _header(response, "set-cookie")))
        self.assertEqual(commands.commands, [])

    def test_adapter_rejects_oversized_headers_and_head_with_cookie_clear(self):
        oversized = AdapterHandler(
            "GET",
            "/api/auth/callback",
            [(f"x-test-{index}", "value") for index in range(65)],
        )
        CallbackHandler._respond(oversized)
        self.assertEqual(oversized.status, 400)
        self.assertIn(("Cache-Control", "no-store"), oversized.response_headers)
        self.assertTrue(
            any(
                name == "Set-Cookie"
                and value.startswith("__Host-cuevion_auth_tx=")
                and "Max-Age=0" in value
                for name, value in oversized.response_headers
            )
        )

        head = AdapterHandler(
            "HEAD",
            "/api/auth/callback",
            [("host", "app.cuevion.com")],
        )
        CallbackHandler.do_HEAD(head)
        self.assertEqual(head.status, 405)
        self.assertEqual(head.wfile.getvalue(), b"")
        self.assertTrue(
            any(name == "Set-Cookie" for name, _value in head.response_headers)
        )


class SessionAndLogoutTests(unittest.TestCase):
    def _stored_session(self, *, security_epoch: int = 3):
        commands = MemoryCommands()
        store = session_store.AuthSessionStore(commands)
        _record, cookie = session_store.create_server_session(
            store,
            secret=ENVIRONMENT["CUEVION_AUTH_SESSION_SECRET"],
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
            security_epoch=security_epoch,
            issuer=ISSUER,
            subject=SUBJECT,
            now=NOW,
            random_bytes=FixedSessionRandom(),
        )
        cookie_pair = cookie.split(";", 1)[0]
        commands.commands.clear()
        return commands, store, (("host", "app.cuevion.com"), ("cookie", cookie_pair))

    def test_shared_member_resolver_returns_canonical_current_account_context(self):
        _commands, store, headers = self._stored_session()
        authority = FakeAuthority(user_result=_user_result())
        resolution = runtime.resolve_authenticated_member(
            headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: store,
            authority_factory=lambda _environment: authority,
        )

        self.assertIs(
            resolution.outcome,
            runtime.MemberResolutionOutcome.AUTHENTICATED,
        )
        self.assertEqual(resolution.set_cookies, ())
        self.assertEqual(
            resolution.member,
            runtime.AuthenticatedMemberContext(
                user_id=USER_ID,
                email=EMAIL,
                name="Cuevion Member",
                workspace_id=WORKSPACE_ID,
                membership_role="member",
            ),
        )
        self.assertEqual(authority.user_calls, [(USER_ID, WORKSPACE_ID)])

    def test_trusted_session_resolver_retains_only_revalidated_server_bindings(self):
        _commands, store, headers = self._stored_session()
        authority = FakeAuthority(user_result=_user_result())
        resolution = runtime.resolve_authenticated_member_session(
            headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: store,
            authority_factory=lambda _environment: authority,
        )

        self.assertIs(
            resolution.outcome,
            runtime.MemberResolutionOutcome.AUTHENTICATED,
        )
        trusted = resolution.session
        self.assertIs(type(trusted), runtime.AuthenticatedMemberSessionContext)
        self.assertEqual(trusted.member.email, EMAIL)
        self.assertEqual(trusted.member.workspace_id, WORKSPACE_ID)
        self.assertEqual(trusted.authentication_version, 1)
        self.assertEqual(trusted.issuer, ISSUER)
        self.assertEqual(trusted.subject, SUBJECT)
        self.assertEqual(trusted.issued_at, NOW)
        self.assertEqual(trusted.expires_at, NOW + session_store.SESSION_TTL_SECONDS)
        self.assertEqual(len(trusted.session_id), 43)
        self.assertEqual(len(trusted.credential_digest), 43)
        self.assertNotIn("__Host-cuevion_session", repr(trusted))
        self.assertNotIn(headers[-1][1], repr(trusted))

    def test_shared_member_resolver_ignores_unrelated_legacy_cookie(self):
        store_factory = mock.Mock(side_effect=AssertionError("must not resolve store"))
        authority_factory = mock.Mock(side_effect=AssertionError("must not read authority"))
        resolution = runtime.resolve_authenticated_member(
            (("cookie", "cuevion_beta_session=legacy"),),
            environment={},
            session_store_factory=store_factory,
            authority_factory=authority_factory,
        )

        self.assertIs(
            resolution.outcome,
            runtime.MemberResolutionOutcome.UNAUTHENTICATED,
        )
        self.assertIsNone(resolution.member)
        self.assertEqual(resolution.set_cookies, ())
        store_factory.assert_not_called()
        authority_factory.assert_not_called()

    def test_shared_member_resolver_malformed_and_expired_sessions_fail_closed(self):
        malformed_commands = MemoryCommands()
        malformed_store = session_store.AuthSessionStore(malformed_commands)
        authority_factory = mock.Mock(side_effect=AssertionError("must not read authority"))
        malformed = runtime.resolve_authenticated_member(
            (("cookie", "__Host-cuevion_session=malformed"),),
            environment=ENVIRONMENT,
            now=NOW,
            session_store_factory=lambda _environment: malformed_store,
            authority_factory=authority_factory,
        )
        self.assertIs(
            malformed.outcome,
            runtime.MemberResolutionOutcome.UNAUTHENTICATED,
        )
        self.assertTrue(any("Max-Age=0" in value for value in malformed.set_cookies))
        self.assertEqual(malformed_commands.commands, [])
        authority_factory.assert_not_called()

        expired_commands, expired_store, expired_headers = self._stored_session()
        expired = runtime.resolve_authenticated_member(
            expired_headers,
            environment=ENVIRONMENT,
            now=NOW + session_store.SESSION_TTL_SECONDS,
            session_store_factory=lambda _environment: expired_store,
            authority_factory=authority_factory,
        )
        self.assertIs(
            expired.outcome,
            runtime.MemberResolutionOutcome.UNAUTHENTICATED,
        )
        self.assertIsNone(expired.member)
        self.assertIn("DEL", [command[0] for command in expired_commands.commands])
        authority_factory.assert_not_called()

    def test_no_auth0_cookie_returns_401_before_runtime_configuration(self):
        response = runtime.session_response(
            "GET",
            (("host", "app.cuevion.com"), ("cookie", "cuevion_beta_session=legacy")),
            environment={},
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(_json(response), {"authenticated": False})

    def test_successful_session_revalidation_returns_only_frontend_view(self):
        commands, store, headers = self._stored_session()
        authority = FakeAuthority(user_result=_user_result())
        response = runtime.session_response(
            "GET",
            headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: store,
            authority_factory=lambda _environment: authority,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            _json(response),
            {
                "authenticated": True,
                "authSource": "auth0",
                "userId": USER_ID,
                "workspaceId": WORKSPACE_ID,
                "email": EMAIL,
                "name": "Cuevion Member",
                "userType": "member",
            },
        )
        self.assertEqual(authority.user_calls, [(USER_ID, WORKSPACE_ID)])
        self.assertNotIn("issuer", _json(response))
        self.assertNotIn("subject", _json(response))

    def test_missing_record_and_security_epoch_mismatch_clear_cookie(self):
        commands, store, headers = self._stored_session()
        commands.values.clear()
        missing = runtime.session_response(
            "GET",
            headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: store,
            authority_factory=lambda _environment: FakeAuthority(user_result=_user_result()),
        )
        self.assertEqual(missing.status, 401)
        self.assertTrue(any("Max-Age=0" in value for value in _header(missing, "set-cookie")))

        mismatch_commands, mismatch_store, mismatch_headers = self._stored_session(
            security_epoch=99
        )
        mismatch = runtime.session_response(
            "GET",
            mismatch_headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: mismatch_store,
            authority_factory=lambda _environment: FakeAuthority(user_result=_user_result()),
        )
        self.assertEqual(mismatch.status, 401)
        self.assertEqual(mismatch_commands.commands[-1][0], "DEL")

    def test_disabled_account_is_revoked_and_unavailable_authority_is_503(self):
        commands, store, headers = self._stored_session()
        denied = runtime.session_response(
            "GET",
            headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: store,
            authority_factory=lambda _environment: FakeAuthority(
                user_result=_user_result(contract.CurrentAccountReadOutcome.NOT_AUTHORIZED)
            ),
        )
        self.assertEqual(denied.status, 401)
        self.assertEqual(commands.commands[-1][0], "DEL")

        inactive_commands, inactive_store, inactive_headers = self._stored_session()
        active = _user_result().authority
        inactive_authority = SimpleNamespace(
            user=SimpleNamespace(
                user_id=active.user.user_id,
                security_epoch=active.user.security_epoch,
                status=models.UserStatus.SUSPENDED,
                display_name=active.user.display_name,
            ),
            primary_verified_email=active.primary_verified_email,
            workspace=active.workspace,
            workspace_membership=active.workspace_membership,
        )
        inactive = runtime.session_response(
            "GET",
            inactive_headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: inactive_store,
            authority_factory=lambda _environment: FakeAuthority(
                user_result=SimpleNamespace(
                    outcome=contract.CurrentAccountReadOutcome.FOUND,
                    authority=inactive_authority,
                )
            ),
        )
        self.assertEqual(inactive.status, 401)
        self.assertEqual(inactive_commands.commands[-1][0], "DEL")

        unavailable_commands, unavailable_store, unavailable_headers = self._stored_session()
        unavailable = runtime.session_response(
            "GET",
            unavailable_headers,
            environment=ENVIRONMENT,
            now=NOW + 1,
            session_store_factory=lambda _environment: unavailable_store,
            authority_factory=lambda _environment: FakeAuthority(
                user_result=_user_result(contract.CurrentAccountReadOutcome.UNAVAILABLE)
            ),
        )
        self.assertEqual(unavailable.status, 503)
        self.assertNotIn("DEL", [command[0] for command in unavailable_commands.commands])

    def test_logout_is_post_same_origin_revokes_and_uses_fixed_return_to(self):
        commands, store, headers = self._stored_session()
        post_headers = (*headers, ("origin", "https://app.cuevion.com"))
        response = runtime.logout_response(
            "POST",
            post_headers,
            environment=ENVIRONMENT,
            session_store_factory=lambda _environment: store,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(commands.commands[-1][0], "DEL")
        payload = _json(response)
        parsed = urlsplit(payload["logoutUrl"])
        self.assertEqual(parsed.hostname, auth0_flow.AUTH0_DOMAIN)
        self.assertEqual(parsed.path, "/v2/logout")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "client_id": [ENVIRONMENT["CUEVION_AUTH0_CLIENT_ID"]],
                "returnTo": ["https://app.cuevion.com/login"],
            },
        )
        self.assertEqual(len(_header(response, "set-cookie")), 2)

    def test_logout_get_and_wrong_origin_do_not_mutate(self):
        commands, store, headers = self._stored_session()
        get_response = runtime.logout_response(
            "GET",
            headers,
            environment=ENVIRONMENT,
            session_store_factory=lambda _environment: store,
        )
        self.assertEqual(get_response.status, 405)
        self.assertEqual(_header(get_response, "set-cookie"), [])
        self.assertEqual(commands.commands, [])
        wrong_origin = runtime.logout_response(
            "POST",
            (*headers, ("origin", "https://evil.example")),
            environment=ENVIRONMENT,
            session_store_factory=lambda _environment: store,
        )
        self.assertEqual(wrong_origin.status, 403)
        self.assertEqual(_header(wrong_origin, "set-cookie"), [])
        self.assertEqual(commands.commands, [])

        duplicate_cookie = runtime.logout_response(
            "POST",
            (
                ("host", "app.cuevion.com"),
                ("origin", "https://app.cuevion.com"),
                headers[1],
                ("cookie", "another=value"),
            ),
            environment=ENVIRONMENT,
            session_store_factory=lambda _environment: store,
        )
        self.assertEqual(duplicate_cookie.status, 400)
        self.assertEqual(_header(duplicate_cookie, "set-cookie"), [])
        self.assertEqual(commands.commands, [])

    def test_logout_revokes_server_session_before_provider_config_failure(self):
        commands, store, headers = self._stored_session()
        incomplete_environment = dict(ENVIRONMENT)
        incomplete_environment.pop("CUEVION_AUTH0_CLIENT_SECRET")
        response = runtime.logout_response(
            "POST",
            (*headers, ("origin", "https://app.cuevion.com")),
            environment=incomplete_environment,
            session_store_factory=lambda _environment: store,
        )
        self.assertEqual(response.status, 503)
        self.assertEqual(commands.commands[-1][0], "DEL")
        self.assertEqual(len(_header(response, "set-cookie")), 2)
        self.assertTrue(
            all("Max-Age=0" in cookie for cookie in _header(response, "set-cookie"))
        )

    def test_all_json_routes_include_security_headers(self):
        response = runtime.session_response(
            "GET",
            (("host", "app.cuevion.com"),),
            environment={},
        )
        self.assertEqual(_header(response, "cache-control"), ["no-store"])
        self.assertEqual(_header(response, "x-content-type-options"), ["nosniff"])
        self.assertEqual(_header(response, "referrer-policy"), ["no-referrer"])
        self.assertEqual(_header(response, "content-type"), ["application/json; charset=utf-8"])


if __name__ == "__main__":
    unittest.main()
