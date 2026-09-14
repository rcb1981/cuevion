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
from api.auth.login import handler as LoginHandler
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
    return auth0_flow.ValidatedIdentityEvidence(
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
        self.assertEqual(query["prompt"], ["login"])
        self.assertNotIn(ENVIRONMENT["CUEVION_AUTH0_CLIENT_SECRET"], location)
        cookie = _header(response, "set-cookie")[0]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_login_adapter_forwards_exact_path_without_frontend_authority(self):
        path = "/api/auth/login?team_invite=tinv_test." + "a" * 43
        headers = [("host", "app.cuevion.com"), ("sec-fetch-site", "same-origin")]
        handler = AdapterHandler("GET", path, headers)
        with mock.patch.object(
            runtime, "login_response", return_value=runtime.http.redirect_response("/login")
        ) as login:
            LoginHandler._respond(handler)
        login.assert_called_once_with("GET", tuple(headers), path)
        self.assertEqual(handler.status, 303)

    def test_public_normal_login_keeps_existing_owner_authorization_request(self):
        responses = [runtime.login_response(
            "GET", (("host", "app.cuevion.com"),), *path,
            environment=ENVIRONMENT, now=NOW,
            random_bytes=FixedTransactionRandom(),
        ) for path in ((), ("/api/auth/login",))]
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[1].status, 303)

    def test_public_login_rejects_malformed_duplicate_and_authority_queries(self):
        token = "tinv_test." + "a" * 43
        for path in (
            "/api/auth/login?team_invite=",
            "/api/auth/login?team_invite=malformed",
            "/api/auth/login?team_invite=%ZZ",
            "/api/auth/login?team_invite=" + token + "%0A",
            "/api/auth/login?team_invite=" + token + "\n",
            "\n/api/auth/login?team_invite=" + token,
            "/api/auth/login?team_invite=" + token + "&team_invite=" + token,
            "/api/auth/login?team_invite=" + token + "&role=owner",
            "/api/auth/login?team_invite=" + token + "&workspaceRole=admin",
            "/api/auth/login?team_invite=" + token + "&workspaceId=wsp_fake",
            "/api/auth/login?team_invite=" + token + "&userId=usr_fake",
            "/api/auth/login?team_invite=" + token + "&memberUserId=usr_fake",
            "/api/auth/login?team_invite=" + token + "&email=recipient@example.com",
            "/api/auth/login?team_invite=" + token + "&returnTo=https://evil.example",
            "/api/auth/login?state=" + token,
            "/api/auth/login?team_invite=" + token + "#fragment",
            "/api/auth/login?team_invite=" + token + "#",
            "https://app.cuevion.com/api/auth/login?team_invite=" + token,
            "/api/auth/other?team_invite=" + token,
            "/api/auth/login?team_invite=" + "a" * 1024,
        ):
            with self.subTest(path=path), mock.patch.object(runtime, "_team_authority") as team:
                response = runtime.login_response(
                    "GET", (("host", "app.cuevion.com"), ("sec-fetch-site", "same-origin")),
                    path, environment={},
                )
            self.assertEqual(response.status, 400)
            self.assertEqual(_header(response, "location"), [])
            self.assertEqual(_header(response, "set-cookie"), [])
            self.assertNotIn(token, response.body.decode())
            team.assert_not_called()

    def test_public_invite_login_requires_unambiguous_same_origin_navigation(self):
        token = "tinv_test." + "a" * 43
        for extra in (
            (), (("sec-fetch-site", "cross-site"),),
            (("sec-fetch-site", "same-site"),), (("sec-fetch-site", "none"),),
            (("origin", "https://evil.example"),),
            (("sec-fetch-site", "same-origin"), ("origin", "https://evil.example")),
            (("sec-fetch-site", "cross-site"), ("origin", "https://app.cuevion.com")),
            (("sec-fetch-site", "same-origin"), ("sec-fetch-site", "same-origin")),
            (("origin", "https://app.cuevion.com"), ("origin", "https://app.cuevion.com")),
        ):
            with self.subTest(extra=extra), mock.patch.object(runtime, "_team_authority") as team:
                response = runtime.login_response(
                    "GET", (("host", "app.cuevion.com"), *extra),
                    "/api/auth/login?team_invite=" + token, environment={},
                )
            self.assertIn(response.status, (400, 403))
            self.assertEqual(_header(response, "location"), [])
            team.assert_not_called()

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

    def test_existing_owner_login_preserves_owner_role_without_provisioning(self):
        current = _authority_result().authority
        membership = current.workspace_membership
        owner_result = contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND,
            contract.CurrentAccountAuthority(
                current.user, current.primary_verified_email,
                current.authentication_identity, current.workspace,
                models.WorkspaceMembership(
                    1, WORKSPACE_ID, USER_ID, models.WorkspaceRole.OWNER,
                    models.WorkspaceMembershipStatus.ACTIVE,
                    membership.created_at, membership.updated_at, membership.row_version,
                ),
            ),
        )
        with mock.patch.object(runtime, "_team_authority", side_effect=AssertionError("normal owner login has no Team reads")):
            response, commands, _reader = self._callback(authority_result=owner_result)
        self.assertEqual(_header(response, "location"), ["/"])
        sessions = [command for command in commands.commands if str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(json.loads(sessions[0][2])["workspaceRole"], "owner")

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
            workspace_role="member",
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
                "workspaceRole": "member",
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

    def test_legacy_owner_session_without_role_still_restores(self):
        commands, store, headers = self._stored_session()
        current = _user_result().authority
        membership = current.workspace_membership
        owner = contract.CurrentAccountByUserAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND,
            contract.CurrentAccountByUserAuthority(
                current.user, current.primary_verified_email, current.workspace,
                models.WorkspaceMembership(
                    1, WORKSPACE_ID, USER_ID, models.WorkspaceRole.OWNER,
                    models.WorkspaceMembershipStatus.ACTIVE,
                    membership.created_at, membership.updated_at, membership.row_version,
                ),
            ),
        )
        for key, raw in list(commands.values.items()):
            payload = json.loads(raw)
            payload.pop("workspaceRole")
            commands.values[key] = json.dumps(payload)
        response = runtime.session_response(
            "GET", headers, environment=ENVIRONMENT, now=NOW + 1,
            session_store_factory=lambda _source: store,
            authority_factory=lambda _source: FakeAuthority(user_result=owner),
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(_json(response)["workspaceRole"], "owner")

    def test_persisted_role_cannot_promote_current_canonical_membership(self):
        commands, store, headers = self._stored_session()
        for key, raw in list(commands.values.items()):
            payload = json.loads(raw)
            payload["workspaceRole"] = "owner"
            commands.values[key] = json.dumps(payload)
        response = runtime.session_response(
            "GET", headers, environment=ENVIRONMENT, now=NOW + 1,
            session_store_factory=lambda _source: store,
            authority_factory=lambda _source: FakeAuthority(user_result=_user_result()),
        )
        self.assertEqual(response.status, 401)


class InviteBoundProvisioningTests(unittest.TestCase):
    TOKEN = "tinv_test." + "a" * 43

    def setUp(self):
        import hashlib
        from api.team.authority import ProvisioningInvitation

        self.invitation = ProvisioningInvitation(
            invitation_id="tinv_test", workspace_id=WORKSPACE_ID,
            email=EMAIL, inviter_user_id=_authority_result().authority.workspace.created_by_user_id,
            token_digest=hashlib.sha256(self.TOKEN.encode()).hexdigest(),
            status="invited", expires_at=(NOW + 60) * 1000,
        )
        self.canonical_result = self._canonical_invitee_authority()
        self.canonical_user_id = self.canonical_result.authority.user.user_id
        self.events = []
        self.prepared = None
        self.active = False
        self.accepted = False
        self.failure = None
        self.proof_count = 0
        self.commands = MemoryCommands()
        self.result_override = None
        self.identity_override = None
        self.initial_override = None
        self.actor = None
        self.repository_calls = 0
        self.prepare_count = 0
        self.accept_count = 0
        self.team = SimpleNamespace(
            read_provisioning_invitation=self._read_invite,
            accept_invitation=self._accept,
            prove_provisioning_acceptance=self._proof,
            prove_provisioned_member=self._current_proof,
        )
        self.repository = SimpleNamespace(prepare=self._prepare, finalize=self._finalize)
        self.reader = SimpleNamespace(resolve_current_account_by_identity=self._resolve)

    def _read_invite(self, token, *, allow_accepted=False):
        from dataclasses import replace

        self.events.append("invite-read")
        self.assertEqual(token, self.TOKEN)
        if self.failure == "invite":
            raise ValueError("rejected")
        return replace(self.invitation, status="accepted" if self.accepted else "invited")

    def _prepare(self, request, *, now):
        self.events.append("prepare")
        self.prepare_count += 1
        self.assertEqual(request.issuer, ISSUER)
        self.assertEqual(request.subject, SUBJECT)
        self.assertEqual(request.email, EMAIL)
        self.assertEqual(request.workspace_id, WORKSPACE_ID)
        self.assertEqual(request.invitation_id, "tinv_test")
        self.assertEqual(now, NOW)
        if self.failure == "prepare":
            raise ValueError("conflict")
        if self.prepared is None:
            authority = self.canonical_result.authority
            self.prepared = SimpleNamespace(
                user_id=authority.user.user_id, email=EMAIL, workspace_id=WORKSPACE_ID,
                security_epoch=authority.user.security_epoch,
                email_id=authority.primary_verified_email.email_id,
                identity_id=authority.authentication_identity.identity_id,
                membership_row_version=authority.workspace_membership.row_version,
            )
        return self.prepared

    def _accept(self, *, actor, token):
        self.events.append("accept")
        self.assertIs(type(actor), runtime.AuthenticatedMemberContext)
        self.assertEqual(actor.user_id, self.prepared.user_id)
        self.assertEqual(actor.workspace_id, self.prepared.workspace_id)
        self.assertEqual(actor.email, self.prepared.email)
        self.assertEqual(actor.membership_role, "member")
        self.assertEqual(token, self.TOKEN)
        self.assertFalse(self.active)
        self.actor = actor
        if self.failure == "before_accept":
            raise RuntimeError("process stopped")
        self.accept_count += 1
        if self.failure == "accept":
            return None, {"code": "cancelled_invite"}
        self.accepted = True
        return {"ok": True}, None

    def _proof(self, token, actor, invitation_id):
        self.events.append("proof")
        self.assertEqual(token, self.TOKEN)
        self.assertEqual(invitation_id, self.invitation.invitation_id)
        self.assertEqual(actor.user_id, self.canonical_user_id)
        self.assertEqual(actor.email, EMAIL)
        self.assertEqual(actor.workspace_id, WORKSPACE_ID)
        if not self.accepted or self.failure == "proof":
            raise ValueError("unproven acceptance")
        self.proof_count += 1
        return True

    def _current_proof(self, actor):
        from dataclasses import replace

        self.events.append("member-proof")
        self.assertEqual(actor.user_id, self.canonical_user_id)
        self.assertEqual(actor.email, EMAIL)
        self.assertEqual(actor.workspace_id, WORKSPACE_ID)
        self.assertEqual(actor.membership_role, "member")
        if not self.accepted or self.failure == "proof":
            raise ValueError("unproven acceptance")
        return replace(self.invitation, status="accepted")

    def _finalize(self, prepared, *, now):
        self.events.append("finalize")
        self.assertTrue(self.accepted)
        self.assertGreater(self.proof_count, 0)
        self.assertIs(prepared, self.prepared)
        if self.failure == "finalize":
            raise RuntimeError("unavailable")
        self.active = True
        if self.failure == "after_finalize":
            raise RuntimeError("process stopped")
        return prepared

    def _resolve(self, key):
        self.events.append("resolve")
        if self.initial_override is not None:
            return self.initial_override
        if self.active:
            return self.result_override or self.canonical_result
        return contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.NOT_AUTHORIZED, None
        )

    def _repository_factory(self, _source):
        self.repository_calls += 1
        return self.repository

    def _command(self, command):
        if str(command[1]).startswith(session_store.SESSION_KEY_PREFIX) and command[0] == "SET":
            self.events.append("session")
            self.assertTrue(self.active)
            self.assertGreater(self.proof_count, 0)
            if self.failure == "session":
                raise session_store.SessionStoreUnavailable()
        return self.commands(command)

    def _callback(self, *, with_invite=True, session_cookie=None):
        transaction = auth0_flow.build_authorization_request(
            auth0_flow.parse_auth0_configuration(ENVIRONMENT), NOW,
            team_invite_token=self.TOKEN if with_invite else None,
        )
        with (
            mock.patch.object(auth0_flow, "exchange_authorization_code", return_value=SimpleNamespace(id_token="trusted-test-token")),
            mock.patch.object(auth0_flow, "validate_id_token_with_jwks", return_value=self.identity_override or _validated_identity()),
        ):
            return runtime.callback_response(
                "GET", _transaction_headers(transaction, session_cookie=session_cookie),
                "/api/auth/callback?code=test-code&state=" + transaction.transaction.state,
                environment=ENVIRONMENT, now=NOW,
                session_store_factory=lambda _source: session_store.AuthSessionStore(self._command),
                authority_factory=lambda _source: self.reader,
                team_authority_factory=lambda _source: self.team,
                invitee_repository_factory=self._repository_factory,
            )

    def _assert_no_session(self, response):
        self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
        self.assertFalse(any(str(command[1]).startswith(session_store.SESSION_KEY_PREFIX) for command in self.commands.commands))
        self.assertFalse(any(cookie.startswith(session_store.SESSION_COOKIE_NAME + "=") for cookie in _header(response, "set-cookie")))
        self.assertNotIn(self.TOKEN, response.body.decode())
        self.assertNotIn(self.TOKEN, str(response.headers))

    def test_success_publishes_one_member_session_after_authoritative_finalize(self):
        response = self._callback()
        self.assertEqual(_header(response, "location"), ["/"])
        self.assertEqual(self.events, ["resolve", "invite-read", "prepare", "accept", "proof", "finalize", "resolve", "proof", "member-proof", "session"])
        writes = [command for command in self.commands.commands if str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)]
        self.assertEqual(len(writes), 1)
        payload = json.loads(writes[0][2])
        self.assertEqual((payload["userId"], payload["workspaceId"], payload["workspaceRole"]), (self.canonical_user_id, WORKSPACE_ID, "member"))
        self.assertEqual(set(self.events), {"resolve", "invite-read", "prepare", "accept", "proof", "finalize", "member-proof", "session"})
        self.assertNotIn(self.TOKEN, str(response.headers))
        self.assertNotIn(self.TOKEN, response.body.decode())
        self.assertFalse(any("continuation" in str(command[1]) for command in self.commands.commands))

    def _existing_canonical_pending_callback(self, *, result=None, store=None):
        transaction = auth0_flow.build_authorization_request(
            auth0_flow.parse_auth0_configuration(ENVIRONMENT), NOW,
            team_invite_token=self.TOKEN,
        )
        repository = mock.Mock(side_effect=AssertionError("existing identity must not provision"))
        with (
            mock.patch.object(auth0_flow, "exchange_authorization_code", return_value=SimpleNamespace(id_token="trusted-token")),
            mock.patch.object(auth0_flow, "validate_id_token_with_jwks", return_value=_validated_identity()),
        ):
            response = runtime.callback_response(
                "GET", _transaction_headers(transaction),
                "/api/auth/callback?code=test&state=" + transaction.transaction.state,
                environment=ENVIRONMENT, now=NOW,
                session_store_factory=lambda _source: store or session_store.AuthSessionStore(self.commands),
                authority_factory=lambda _source: FakeAuthority(identity_result=result or _authority_result()),
                team_authority_factory=lambda _source: self.team,
                invitee_repository_factory=repository,
                random_bytes=FixedSessionRandom(),
            )
        repository.assert_not_called()
        return response

    def test_existing_canonical_pending_invite_uses_fixed_marker_and_exact_session_binding(self):
        for role in (models.WorkspaceRole.OWNER, models.WorkspaceRole.ADMIN, models.WorkspaceRole.MEMBER):
            with self.subTest(role=role.value):
                self.setUp()
                authority = _authority_result().authority
                membership = authority.workspace_membership
                result = contract.CurrentAccountAuthorityResult(
                    contract.CurrentAccountReadOutcome.FOUND,
                    contract.CurrentAccountAuthority(
                        authority.user, authority.primary_verified_email,
                        authority.authentication_identity, authority.workspace,
                        models.WorkspaceMembership(
                            1, WORKSPACE_ID, USER_ID, role,
                            models.WorkspaceMembershipStatus.ACTIVE,
                            membership.created_at, membership.updated_at, membership.row_version,
                        ),
                    ),
                )
                response = self._existing_canonical_pending_callback(result=result)
                self.assertEqual(_header(response, "location"), ["/?team_continue=1"])
                self.assertNotIn(self.TOKEN, str(response.headers))
                self.assertNotIn(self.TOKEN, response.body.decode())
                self.assertEqual(self.accept_count, 0)
                self.assertEqual(self.prepare_count, 0)
                cookie = next(value for value in _header(response, "set-cookie")
                              if value.startswith(session_store.SESSION_COOKIE_NAME + "="))
                store = session_store.AuthSessionStore(self.commands)
                record, _ = session_store.load_server_session(
                    store, headers=(("cookie", cookie.split(";", 1)[0]),),
                    secret=ENVIRONMENT["CUEVION_AUTH_SESSION_SECRET"], now=NOW,
                )
                binding = session_store.TeamInviteContinuationBinding(
                    record.session_id, record.user_id, record.workspace_id,
                    record.issuer, record.subject, record.created_at, record.expires_at,
                )
                continuation = store.get_team_invite_continuation(
                    binding, secret=ENVIRONMENT["CUEVION_AUTH_SESSION_SECRET"], now=NOW,
                )
                self.assertEqual(continuation.binding, binding)
                self.assertEqual(record.workspace_role, role.value)
                self.assertEqual(continuation.raw_invite_token, self.TOKEN)
                self.assertEqual(continuation.invitation_id, self.invitation.invitation_id)
                self.assertEqual(continuation.token_digest, self.invitation.token_digest)
                self.assertEqual(continuation.invitee_email, EMAIL)
                self.assertEqual(continuation.inviter_user_id, self.invitation.inviter_user_id)
                self.assertEqual(continuation.expires_at, NOW + 60)
                self.assertNotIn(self.TOKEN, repr(continuation))
                self.assertTrue(any(self.TOKEN in str(command[2]) for command in self.commands.commands if command[0] == "SET"))

    def test_continuation_persistence_failure_never_publishes_new_session_cookie(self):
        for outcome in (False, session_store.SessionStoreUnavailable()):
            with self.subTest(outcome=type(outcome).__name__):
                self.setUp()
                store = session_store.AuthSessionStore(self.commands)
                with mock.patch.object(
                    session_store.AuthSessionStore, "put_team_invite_continuation",
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=outcome if not isinstance(outcome, Exception) else None,
                ):
                    response = self._existing_canonical_pending_callback(store=store)
                self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
                self.assertFalse(any(value.startswith(session_store.SESSION_COOKIE_NAME + "=")
                                     for value in _header(response, "set-cookie")))
                self.assertNotIn(self.TOKEN, str(response.headers))
                self.assertNotIn(self.TOKEN, response.body.decode())
                self.assertEqual(self.accept_count, 0)

    def test_existing_canonical_continuation_requires_exact_verified_recipient(self):
        from dataclasses import replace

        self.invitation = replace(self.invitation, email="different@example.com")
        response = self._existing_canonical_pending_callback()
        self._assert_no_session(response)
        self.assertFalse(any("continuation" in str(command[1]) for command in self.commands.commands))

    def test_new_noninvited_identity_never_constructs_writer(self):
        self._assert_no_session(self._callback(with_invite=False))
        self.assertEqual(self.repository_calls, 0)
        self.assertEqual(self.events, ["resolve"])

    def test_uncertain_or_malformed_authority_never_provisions(self):
        for outcome in (contract.CurrentAccountReadOutcome.UNAVAILABLE, contract.CurrentAccountReadOutcome.INTERNAL_ERROR):
            self.initial_override = contract.CurrentAccountAuthorityResult(outcome, None)
            self._assert_no_session(self._callback())
        self.initial_override = SimpleNamespace(outcome=contract.CurrentAccountReadOutcome.NOT_AUTHORIZED, authority=None)
        self._assert_no_session(self._callback())
        self.assertEqual(self.repository_calls, 0)

    def test_wrong_email_or_invalid_invite_never_prepares(self):
        self.identity_override = _validated_identity(email="wrong@example.com")
        self._assert_no_session(self._callback())
        self.identity_override = None
        self.failure = "invite"
        self._assert_no_session(self._callback())
        self.assertEqual(self.repository_calls, 0)

    def test_case1_prepare_then_crash_leaves_inert_graph_and_retry_resumes(self):
        self.failure = "before_accept"
        self._assert_no_session(self._callback())
        original = self.prepared
        self.assertFalse(self.active)
        self.assertFalse(self.accepted)
        self.failure = None
        response = self._callback()
        self.assertEqual(_header(response, "location"), ["/"])
        self.assertIs(self.prepared, original)
        self.assertEqual(self.accept_count, 1)

    def test_case2_accept_rejection_leaves_inert_account_and_no_session(self):
        self.failure = "accept"
        self._assert_no_session(self._callback())
        self.assertFalse(self.active)
        self.assertFalse(self.accepted)
        self.assertNotIn("finalize", self.events)

    def test_case3_accepted_then_finalize_failure_recovers_without_reaccept(self):
        self.failure = "finalize"
        self._assert_no_session(self._callback())
        self.assertTrue(self.accepted)
        self.assertFalse(self.active)
        self.failure = None
        self.assertEqual(_header(self._callback(), "location"), ["/"])
        self.assertEqual(self.accept_count, 1)

    def test_recovery_requires_exact_accepted_proof(self):
        self.accepted = True
        self.failure = "proof"
        self._assert_no_session(self._callback())
        self.assertNotIn("finalize", self.events)
        self.assertFalse(self.active)

    def test_case4_finalize_commit_then_crash_recovers_through_normal_authority(self):
        self.failure = "after_finalize"
        self._assert_no_session(self._callback())
        self.assertTrue(self.active)
        prepares = self.prepare_count
        self.failure = None
        self.assertEqual(_header(self._callback(), "location"), ["/"])
        self.assertEqual(self.prepare_count, prepares)
        self.assertEqual(self.accept_count, 1)

    def test_case5_failed_session_preserves_durable_graph_for_next_login(self):
        self.failure = "session"
        response = self._callback()
        self.assertEqual(_header(response, "location"), ["/login?error=authentication_failed"])
        self.assertTrue(self.active)
        self.assertTrue(self.accepted)
        self.failure = None
        self.assertEqual(_header(self._callback(), "location"), ["/"])
        self.assertEqual(self.prepare_count, 1)
        self.assertEqual(self.accept_count, 1)

    def test_finalize_authoritative_read_mismatch_prevents_session(self):
        from api.auth.test_account_authority import OTHER_USER_ID, OTHER_WORKSPACE_ID

        for result in (_authority_result(user_id=OTHER_USER_ID), _authority_result(workspace_id=OTHER_WORKSPACE_ID)):
            self.setUp()
            self.result_override = result
            self._assert_no_session(self._callback())

    def test_existing_different_workspace_cannot_switch_or_provision(self):
        from api.auth.test_account_authority import OTHER_WORKSPACE_ID

        self.initial_override = _authority_result(workspace_id=OTHER_WORKSPACE_ID)
        self._assert_no_session(self._callback())
        self.assertEqual(self.repository_calls, 0)

    def test_pending_login_validates_before_redirect_and_keeps_state_opaque(self):
        response = runtime.login_response(
            "GET", (("host", "app.cuevion.com"),),
            environment=ENVIRONMENT, now=NOW, team_invite_token=self.TOKEN,
            team_authority_factory=lambda _source: self.team,
        )
        self.assertEqual(response.status, 303)
        self.assertEqual(self.events, ["invite-read"])
        location = _header(response, "location")[0]
        self.assertNotIn(self.TOKEN, location)
        state = parse_qs(urlsplit(location).query)["state"][0]
        self.assertEqual(len(state), 43)
        self.assertNotIn("tinv_", state)
        cookie = _header(response, "set-cookie")[0].split(";", 1)[0].split("=", 1)[1]
        tx = auth0_flow.consume_transaction_cookie(cookie, state, auth0_flow.parse_auth0_configuration(ENVIRONMENT), NOW)
        self.assertEqual(tx.team_invite_token, self.TOKEN)

    def test_public_invite_login_seals_context_and_forces_account_reauthentication(self):
        for origin_headers in (
            (("sec-fetch-site", "same-origin"),),
            (("origin", "https://app.cuevion.com"),),
        ):
            self.events.clear()
            response = runtime.login_response(
                "GET", (("host", "app.cuevion.com"), *origin_headers,
                        ("cookie", "__Host-cuevion_session=existing-account")),
                "/api/auth/login?team_invite=" + self.TOKEN,
                environment=ENVIRONMENT, now=NOW,
                random_bytes=FixedTransactionRandom(),
                team_authority_factory=lambda _source: self.team,
            )
            self.assertEqual(response.status, 303)
            self.assertEqual(self.events, ["invite-read"])
            location = _header(response, "location")[0]
            query = parse_qs(urlsplit(location).query)
            self.assertEqual(query["prompt"], ["login"])
            self.assertEqual(query["connection"], ["email"])
            self.assertNotIn(self.TOKEN, location)
            self.assertNotIn(self.TOKEN, query["state"][0])
            cookies = _header(response, "set-cookie")
            self.assertEqual(len(cookies), 1)
            self.assertNotIn(self.TOKEN, cookies[0])
            self.assertIn("Max-Age=600; Secure; HttpOnly; SameSite=Lax", cookies[0])
            cookie = cookies[0].split(";", 1)[0].split("=", 1)[1]
            tx = auth0_flow.consume_transaction_cookie(
                cookie, query["state"][0],
                auth0_flow.parse_auth0_configuration(ENVIRONMENT), NOW,
            )
            self.assertEqual(tx.team_invite_token, self.TOKEN)

    def _existing_account_cookie(self):
        store = session_store.AuthSessionStore(self.commands)
        record, cookie = session_store.create_server_session(
            store, secret=ENVIRONMENT["CUEVION_AUTH_SESSION_SECRET"],
            user_id=USER_ID, workspace_id=WORKSPACE_ID,
            security_epoch=3, workspace_role="owner",
            issuer=ISSUER, subject="auth0|existing-other-account", now=NOW,
        )
        self.commands.commands.clear()
        return record, cookie.split(";", 1)[0]

    def test_invite_account_switch_revokes_old_session_only_after_recipient_finalize(self):
        old_record, old_cookie = self._existing_account_cookie()
        response = self._callback(session_cookie=old_cookie)
        self.assertEqual(_header(response, "location"), ["/"])
        session_commands = [command for command in self.commands.commands
                            if str(command[1]).startswith(session_store.SESSION_KEY_PREFIX)]
        self.assertEqual([command[0] for command in session_commands], ["DEL", "SET"])
        payload = json.loads(session_commands[-1][2])
        self.assertEqual(payload["userId"], self.canonical_user_id)
        self.assertNotEqual(payload["userId"], old_record.user_id)
        self.assertEqual(payload["workspaceRole"], "member")
        self.assertEqual(payload["subject"], SUBJECT)
        self.assertLess(self.events.index("finalize"), self.events.index("session"))
        self.assertEqual(self.accept_count, 1)

    def test_wrong_recipient_switch_keeps_existing_account_and_creates_no_authority(self):
        _record, old_cookie = self._existing_account_cookie()
        original_records = dict(self.commands.values)
        self.identity_override = _validated_identity(email="wrong@example.com")
        self._assert_no_session(self._callback(session_cookie=old_cookie))
        self.assertEqual(self.repository_calls, 0)
        self.assertEqual(self.prepare_count, 0)
        self.assertEqual(self.accept_count, 0)
        for key, value in original_records.items():
            self.assertEqual(self.commands.values[key], value)

    def test_unusable_invites_fail_before_auth0_redirect_without_secret_text(self):
        from api.team.authority import TeamAuthorityError

        for code in ("invalid_invite", "expired_invite", "cancelled_invite", "declined_invite", "forbidden"):
            team = SimpleNamespace(read_provisioning_invitation=mock.Mock(side_effect=TeamAuthorityError(code)))
            response = runtime.login_response(
                "GET", (("host", "app.cuevion.com"),),
                environment=ENVIRONMENT, now=NOW, team_invite_token=self.TOKEN,
                team_authority_factory=lambda _source: team,
            )
            self.assertEqual(response.status, 503)
            self.assertEqual(_header(response, "location"), [])
            self.assertNotIn(self.TOKEN, response.body.decode())

    def test_entry_point_accepts_only_token_and_operation(self):
        from api.team import invite

        for query in (
            "op=authenticate&token=" + self.TOKEN + "&workspaceRole=owner",
            "op=authenticate&token=" + self.TOKEN + "&memberUserId=usr_fake",
            "op=authenticate&token=" + self.TOKEN + "&returnTo=https://evil.example",
            "op=authenticate&token=" + self.TOKEN + "&token=other",
        ):
            handler = AdapterHandler("GET", "/api/team/invite?" + query, [("host", "app.cuevion.com")])
            with mock.patch.object(invite.auth_runtime, "login_response") as login:
                invite._handle_invite_authentication(handler)
            login.assert_not_called()
            self.assertEqual(handler.status, 400)
            self.assertNotIn(self.TOKEN, handler.wfile.getvalue().decode())
        handler = AdapterHandler("GET", "/api/team/invite?op=authenticate&token=" + self.TOKEN, [("host", "app.cuevion.com")])
        with mock.patch.object(invite.auth_runtime, "login_response", return_value=runtime.http.redirect_response("/login")) as login:
            invite._handle_invite_authentication(handler)
        self.assertEqual(login.call_args.kwargs, {"team_invite_token": self.TOKEN})

    def _preparation_request(self, invitation=None):
        from cuevion_db.postgresql_team_invitee_repository import InviteePreparationRequest

        invitation = self.invitation if invitation is None else invitation
        return InviteePreparationRequest(
            ISSUER, SUBJECT, invitation.email, invitation.workspace_id,
            invitation.invitation_id, invitation.token_digest, invitation.inviter_user_id,
        )

    def _canonical_invitee_authority(self, *, verification_source=None):
        from cuevion_db.postgresql_team_invitee_repository import derive_invitee_provenance, derive_invitee_record_ids

        request = self._preparation_request()
        user_id, email_id, identity_id = derive_invitee_record_ids(request)
        provenance = derive_invitee_provenance(request) if verification_source is None else verification_source
        return contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND,
            contract.CurrentAccountAuthority(
                models.CuevionUser(1, user_id, models.UserStatus.ACTIVE, email_id, "Team member", 1, NOW, NOW, 1),
                models.VerifiedEmail(1, email_id, user_id, EMAIL, models.VerifiedEmailStatus.VERIFIED, provenance, NOW, NOW, None, 1),
                models.AuthenticationIdentity(1, identity_id, user_id, ISSUER, SUBJECT, models.AuthenticationMethod.OIDC, models.AuthenticationIdentityStatus.ACTIVE, email_id, NOW, None, 1),
                _authority_result().authority.workspace,
                models.WorkspaceMembership(1, WORKSPACE_ID, user_id, models.WorkspaceRole.MEMBER, models.WorkspaceMembershipStatus.ACTIVE, NOW, NOW, 2),
            ),
        )

    def _tokenless_callback(self, result, proof, *, identity=None):
        from dataclasses import replace

        if not isinstance(proof, Exception):
            proof = replace(proof, status="accepted")
        commands = MemoryCommands()
        team = SimpleNamespace(prove_provisioned_member=mock.Mock(
            side_effect=proof if isinstance(proof, Exception) else None,
            return_value=None if isinstance(proof, Exception) else proof,
        ))
        writer = mock.Mock(side_effect=AssertionError("existing authority cannot provision"))
        tx = _transaction_request()
        with (
            mock.patch.object(auth0_flow, "exchange_authorization_code", return_value=SimpleNamespace(id_token="test-token")),
            mock.patch.object(auth0_flow, "validate_id_token_with_jwks", return_value=identity or _validated_identity()),
        ):
            response = runtime.callback_response(
                "GET", _transaction_headers(tx),
                "/api/auth/callback?code=test&state=" + tx.transaction.state,
                environment=ENVIRONMENT, now=NOW,
                session_store_factory=lambda _source: session_store.AuthSessionStore(commands),
                authority_factory=lambda _source: FakeAuthority(identity_result=result),
                team_authority_factory=lambda _source: team,
                invitee_repository_factory=writer,
            )
        writer.assert_not_called()
        return response, commands, team

    def _assert_tokenless_result(self, response, commands, *, succeeds):
        self.assertEqual(_header(response, "location"), ["/" if succeeds else "/login?error=authentication_failed"])
        sessions = [cmd for cmd in commands.commands if str(cmd[1]).startswith(session_store.SESSION_KEY_PREFIX)]
        self.assertEqual(len(sessions), 1 if succeeds else 0)
        cookies = [cookie for cookie in _header(response, "set-cookie") if cookie.startswith(session_store.SESSION_COOKIE_NAME + "=")]
        self.assertEqual(len(cookies), 1 if succeeds else 0)
        self.assertNotIn(self.invitation.token_digest, repr(response))
        self.assertNotIn("team-invite-oidc", repr(response))

    def test_tokenless_finalized_login_requires_current_exact_team_incarnation(self):
        from dataclasses import replace
        from api.auth.test_account_authority import OTHER_WORKSPACE_ID
        from api.team.authority import TeamAuthorityError
        from cuevion_db.postgresql_team_invitee_repository import derive_invitee_provenance, derive_invitee_record_ids

        original = self._preparation_request()
        result = self.canonical_result
        for changes in (
            {"invitation_id": "tinv_replacement"},
            {"token_digest": "0" * 64},
            {"inviter_user_id": USER_ID},
            {"workspace_id": OTHER_WORKSPACE_ID},
        ):
            with self.subTest(changes=changes):
                replacement = replace(self.invitation, **changes)
                request = self._preparation_request(replacement)
                self.assertEqual(derive_invitee_record_ids(original), derive_invitee_record_ids(request))
                self.assertNotEqual(derive_invitee_provenance(original), derive_invitee_provenance(request))
                response, commands, team = self._tokenless_callback(result, replacement)
                team.prove_provisioned_member.assert_called_once()
                self._assert_tokenless_result(response, commands, succeeds=False)
        for proof, succeeds in (
            (replace(self.invitation, status="accepted"), True),
            (TeamAuthorityError("team_member_not_active"), False),
        ):
            response, commands, _team = self._tokenless_callback(result, proof)
            self._assert_tokenless_result(response, commands, succeeds=succeeds)

    def test_legacy_malformed_or_unsupported_team_provenance_cannot_bypass_gate(self):
        for source in (
            "team-invite-oidc",
            "TEAM-INVITE-OIDC:v1:" + "a" * 64,
            "team-invite-oidc:v0:" + "a" * 64,
            "team-invite-oidc:v2:" + "a" * 64,
            "team-invite-oidc:v1:" + "a" * 63,
            "team-invite-oidc:v1:" + "A" * 64,
            "team-invite-oidc:unknown",
            "team-invite-oidc-extra",
        ):
            with self.subTest(source=source):
                result = self._canonical_invitee_authority(verification_source=source)
                response, commands, team = self._tokenless_callback(result, self.invitation)
                self._assert_tokenless_result(response, commands, succeeds=False)
                team.prove_provisioned_member.assert_not_called()

    def test_well_formed_but_wrong_team_provenance_still_requires_exact_match(self):
        result = self._canonical_invitee_authority(
            verification_source="team-invite-oidc:v1:" + "0" * 64,
        )
        response, commands, team = self._tokenless_callback(result, self.invitation)
        self._assert_tokenless_result(response, commands, succeeds=False)
        team.prove_provisioned_member.assert_called_once()

    def test_fresh_provisioning_requires_durable_provenance_in_canonical_read(self):
        from dataclasses import replace
        from cuevion_db.postgresql_team_invitee_repository import derive_invitee_provenance

        wrong_incarnation = derive_invitee_provenance(replace(
            self._preparation_request(), invitation_id="tinv_replacement",
        ))
        for source in ("auth0-oidc", "team-invite-oidc", wrong_incarnation):
            with self.subTest(source=source):
                self.setUp()
                self.result_override = self._canonical_invitee_authority(verification_source=source)
                self._assert_no_session(self._callback())
                self.assertTrue(self.active)
                self.assertIn("finalize", self.events)
                self.assertNotIn("session", self.events)
                self.assertNotIn("member-proof", self.events)

    def test_changed_verified_email_cannot_rewrite_existing_identity_or_create_user(self):
        identity = _validated_identity(email="changed@example.test")
        response, commands, team = self._tokenless_callback(
            self.canonical_result, self.invitation, identity=identity,
        )
        self._assert_tokenless_result(response, commands, succeeds=False)
        team.prove_provisioned_member.assert_not_called()
        self.initial_override = self.canonical_result
        self.identity_override = identity
        self._assert_no_session(self._callback())
        self.assertEqual(self.repository_calls, 0)

    def test_team_provenance_never_publishes_owner_or_admin_session(self):
        from dataclasses import replace

        for role in (models.WorkspaceRole.OWNER, models.WorkspaceRole.ADMIN):
            with self.subTest(role=role):
                authority = self.canonical_result.authority
                result = contract.CurrentAccountAuthorityResult(
                    contract.CurrentAccountReadOutcome.FOUND,
                    contract.CurrentAccountAuthority(
                        authority.user, authority.primary_verified_email,
                        authority.authentication_identity, authority.workspace,
                        replace(authority.workspace_membership, role=role),
                    ),
                )
                response, commands, team = self._tokenless_callback(result, self.invitation)
                self._assert_tokenless_result(response, commands, succeeds=False)
                team.prove_provisioned_member.assert_not_called()

    def test_existing_owner_login_ignores_team_only_provenance_gate(self):
        from dataclasses import replace

        result = _authority_result()
        authority = result.authority
        result = contract.CurrentAccountAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND,
            contract.CurrentAccountAuthority(
                authority.user, authority.primary_verified_email,
                authority.authentication_identity, authority.workspace,
                replace(authority.workspace_membership, role=models.WorkspaceRole.OWNER),
            ),
        )
        response, commands, team = self._tokenless_callback(
            result, AssertionError("ordinary owner must not read Team authority"),
        )
        self._assert_tokenless_result(response, commands, succeeds=True)
        team.prove_provisioned_member.assert_not_called()
        payload = json.loads(next(cmd[2] for cmd in commands.commands if str(cmd[1]).startswith(session_store.SESSION_KEY_PREFIX)))
        self.assertEqual(payload["workspaceRole"], "owner")
        self.assertEqual(payload["userId"], USER_ID)

    def test_new_team_session_restore_uses_canonical_role_without_new_team_io(self):
        authority = self._canonical_invitee_authority().authority
        result = contract.CurrentAccountByUserAuthorityResult(
            contract.CurrentAccountReadOutcome.FOUND,
            contract.CurrentAccountByUserAuthority(
                authority.user, authority.primary_verified_email,
                authority.workspace, authority.workspace_membership,
            ),
        )
        commands = MemoryCommands()
        store = session_store.AuthSessionStore(commands)
        _record, cookie = session_store.create_server_session(
            store, secret=ENVIRONMENT["CUEVION_AUTH_SESSION_SECRET"],
            user_id=authority.user.user_id, workspace_id=WORKSPACE_ID,
            security_epoch=1, issuer=ISSUER, subject=SUBJECT,
            now=NOW, workspace_role="member",
        )
        headers = (("host", "app.cuevion.com"), ("cookie", cookie.split(";", 1)[0]))
        resolve = lambda: runtime.resolve_authenticated_member_session(
            headers, environment=ENVIRONMENT, now=NOW + 1,
            session_store_factory=lambda _source: store,
            authority_factory=lambda _source: FakeAuthority(user_result=result),
        )
        with mock.patch.object(runtime, "_team_authority", side_effect=AssertionError("no new per-message Team reads")):
            accepted = resolve()
        self.assertIs(accepted.outcome, runtime.MemberResolutionOutcome.AUTHENTICATED)
        self.assertEqual(accepted.session.member.email, EMAIL)
        self.assertEqual(accepted.session.member.membership_role, "member")

    def test_config_read_uses_member_email_without_inviter_workspace_fallback(self):
        from api import user_config_store

        member = runtime.AuthenticatedMemberContext(
            user_id=USER_ID, email="invitee@example.com", name="Invitee",
            workspace_id=WORKSPACE_ID, membership_role="member",
        )
        storage = {"owner@example.com": {"inboxes": [{"id": "owner-mailbox"}]}}
        requested = []
        def read(_store, email):
            requested.append(email)
            config = storage.get(email)
            return {"status": "ok" if config else "not_found", "config": config, "error": None}
        with (
            mock.patch.object(user_config_store, "resolve_authenticated_member_authority", return_value=(member, None)),
            mock.patch.object(user_config_store, "resolve_user_config_store", return_value=(storage, None)),
            mock.patch.object(user_config_store, "read_user_config_record", side_effect=read),
        ):
            resolved, user, result = user_config_store.read_user_config_for_authenticated_member(())
        self.assertIs(resolved, member)
        self.assertEqual(user["email"], "invitee@example.com")
        self.assertEqual(requested, ["invitee@example.com"])
        self.assertIsNone(result["config"])
        self.assertEqual(storage, {"owner@example.com": {"inboxes": [{"id": "owner-mailbox"}]}})

    def test_writer_configuration_never_falls_back_to_reader(self):
        for environment in ({}, {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": "reader"}, {
            "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": "same",
            "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": "same",
        }):
            with self.assertRaises(account_authority.AccountAuthorityConfigurationError):
                account_authority.build_runtime_team_invitee_repository(environment)


if __name__ == "__main__":
    unittest.main()
