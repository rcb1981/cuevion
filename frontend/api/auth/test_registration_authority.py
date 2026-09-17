"""Offline tests for single-use Team-invite registration authority."""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from api.auth import auth0_flow, http, registration_authority


NOW = 1_800_000_000
CLIENT_ID = "registration-authority-test-client"
SHARED_SECRET = "registration-authority-secret-" + "x" * 32
STATE = "S" * 43
GRANT = "G" * 43
EMAIL = "invitee@example.com"
INVITATION_ID = "tinv_registration_test"
TOKEN_DIGEST = "a" * 64
ENVIRONMENT = {
    "CUEVION_AUTH0_DOMAIN": auth0_flow.AUTH0_DOMAIN,
    "CUEVION_AUTH0_CLIENT_ID": CLIENT_ID,
    "CUEVION_AUTH0_CLIENT_SECRET": "registration-authority-client-secret",
    "CUEVION_AUTH_SESSION_SECRET": "R" * 48,
    "CUEVION_AUTH0_REGISTRATION_AUTHORITY_SECRET": SHARED_SECRET,
}


class MemoryCommands:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        operation = command[0]
        if operation == "SET":
            key = str(command[1])
            if command[-1] == "NX" and key in self.values:
                return {"result": None}
            self.values[key] = str(command[2])
            return {"result": "OK"}
        if operation == "GET":
            return {"result": self.values.get(str(command[1]))}
        if operation == "EVAL":
            key = str(command[3])
            expected = str(command[4])
            current = self.values.get(key)
            if current is None or current != expected:
                return {"result": 0}
            del self.values[key]
            return {"result": 1}
        raise AssertionError(operation)


class RegistrationAuthorityTests(unittest.TestCase):
    def _record(self, **changes):
        values = {
            "source": "team_invite",
            "email": EMAIL,
            "client_id": CLIENT_ID,
            "authority_id": INVITATION_ID,
            "authority_digest": TOKEN_DIGEST,
            "created_at": NOW,
            "expires_at": NOW + 600,
        }
        values.update(changes)
        return registration_authority.RegistrationGrantRecord(**values)

    def _headers(self, *, secret=SHARED_SECRET):
        return (
            ("host", "app.cuevion.com"),
            ("authorization", "Bearer " + secret),
            ("content-type", "application/json"),
        )

    def _body(self, **changes):
        value = {
            "acrValue": registration_authority.ACR_PREFIX + GRANT,
            "clientId": CLIENT_ID,
            "email": EMAIL,
        }
        value.update(changes)
        return json.dumps(value, separators=(",", ":")).encode()

    def _store_with_grant(self):
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        self.assertTrue(store.put(GRANT, self._record(), now=NOW))
        return commands, store

    def test_grant_store_is_opaque_nx_time_bounded_and_single_use(self):
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        record = self._record()
        self.assertTrue(store.put(GRANT, record, now=NOW))
        self.assertFalse(store.put(GRANT, record, now=NOW))
        self.assertEqual(store.get(GRANT, now=NOW + 599), record)
        self.assertTrue(store.consume(GRANT, record, now=NOW + 1))
        self.assertIsNone(store.get(GRANT, now=NOW + 1))
        self.assertFalse(store.consume(GRANT, record, now=NOW + 1))
        key = str(commands.commands[0][1])
        self.assertTrue(key.startswith(registration_authority.GRANT_KEY_PREFIX))
        self.assertNotIn(GRANT, key)
        self.assertNotIn(EMAIL, key)

    def test_issue_team_invite_grant_binds_email_client_and_invite(self):
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        grant = registration_authority.issue_team_invite_grant(
            store,
            email=" Invitee@Example.COM ",
            client_id=CLIENT_ID,
            invitation_id=INVITATION_ID,
            invitation_token_digest=TOKEN_DIGEST,
            invitation_expires_at_ms=(NOW + 300) * 1000,
            now=NOW,
            random_bytes=lambda count: b"Q" * count,
        )
        self.assertEqual(len(grant), 43)
        record = store.get(grant, now=NOW)
        self.assertIsNotNone(record)
        self.assertEqual(record.email, EMAIL)
        self.assertEqual(record.client_id, CLIENT_ID)
        self.assertEqual(record.authority_id, INVITATION_ID)
        self.assertEqual(record.authority_digest, TOKEN_DIGEST)
        self.assertEqual(record.source, "team_invite")
        self.assertEqual(record.expires_at, NOW + 300)

    def test_normal_login_is_exact_passthrough(self):
        expected = http.redirect_response("https://example.test/login")
        with mock.patch.object(
            registration_authority.runtime,
            "login_response",
            return_value=expected,
        ) as established:
            response = registration_authority.login_response(
                "GET", (("host", "app.cuevion.com"),), "/api/auth/login"
            )
        self.assertEqual(response, expected)
        established.assert_called_once_with(
            "GET", (("host", "app.cuevion.com"),), "/api/auth/login"
        )

    def test_validated_team_invite_redirect_gets_server_backed_grant(self):
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        location = (
            f"https://{auth0_flow.AUTH0_DOMAIN}/authorize?"
            f"response_type=code&client_id={CLIENT_ID}&state={STATE}"
        )
        established_response = http.redirect_response(
            location,
            set_cookies=("__Host-cuevion_auth_tx=opaque; Secure; HttpOnly",),
        )
        invitation = SimpleNamespace(
            email=EMAIL,
            invitation_id=INVITATION_ID,
            token_digest=TOKEN_DIGEST,
            expires_at=(NOW + 600) * 1000,
        )
        team = SimpleNamespace(
            read_provisioning_invitation=mock.Mock(return_value=invitation)
        )
        token = INVITATION_ID + "." + "a" * 43
        with (
            mock.patch.dict(os.environ, ENVIRONMENT, clear=True),
            mock.patch.object(
                registration_authority.runtime,
                "login_response",
                return_value=established_response,
            ),
            mock.patch.object(
                registration_authority.runtime,
                "_team_authority",
                return_value=team,
            ),
            mock.patch.object(
                registration_authority,
                "build_runtime_registration_store",
                return_value=store,
            ),
            mock.patch.object(registration_authority.time, "time", return_value=NOW),
            mock.patch.object(
                registration_authority.secrets,
                "token_bytes",
                return_value=b"Q" * 32,
            ),
        ):
            response = registration_authority.login_response(
                "GET",
                (("host", "app.cuevion.com"), ("sec-fetch-site", "same-origin")),
                "/api/auth/login?team_invite=" + token,
            )
        self.assertEqual(response.status, 303)
        redirect = [
            value for name, value in response.headers if name.lower() == "location"
        ][0]
        query = parse_qs(urlsplit(redirect).query)
        self.assertEqual(query["state"], [STATE])
        self.assertNotIn("correlation_id", query)
        self.assertEqual(len(query["acr_values"]), 1)
        acr_value = query["acr_values"][0]
        self.assertTrue(acr_value.startswith(registration_authority.ACR_PREFIX))
        grant = acr_value[len(registration_authority.ACR_PREFIX) :]
        record = store.get(grant, now=NOW)
        self.assertIsNotNone(record)
        self.assertEqual(record.email, EMAIL)
        self.assertEqual(record.client_id, CLIENT_ID)
        self.assertEqual(record.authority_id, INVITATION_ID)
        self.assertEqual(record.authority_digest, TOKEN_DIGEST)
        team.read_provisioning_invitation.assert_called_once_with(
            token, allow_accepted=True
        )
        self.assertTrue(
            any(name.lower() == "set-cookie" for name, _ in response.headers)
        )

    def test_ambiguous_authorization_context_fails_closed(self):
        token = INVITATION_ID + "." + "a" * 43
        for parameter in ("acr_values=foreign", "correlation_id=foreign"):
            location = (
                f"https://{auth0_flow.AUTH0_DOMAIN}/authorize?"
                f"response_type=code&client_id={CLIENT_ID}&state={STATE}&{parameter}"
            )
            original = http.redirect_response(
                location,
                set_cookies=("__Host-cuevion_auth_tx=opaque; Secure; HttpOnly",),
            )
            with mock.patch.object(
                registration_authority.runtime,
                "login_response",
                return_value=original,
            ):
                denied = registration_authority.login_response(
                    "GET",
                    (("host", "app.cuevion.com"),),
                    "/api/auth/login?team_invite=" + token,
                )
            self.assertEqual(denied.status, 503)
            self.assertFalse(
                any(name.lower() == "set-cookie" for name, _ in denied.headers)
            )

    def test_grant_setup_failure_never_sends_auth_transaction_cookie(self):
        location = (
            f"https://{auth0_flow.AUTH0_DOMAIN}/authorize?"
            f"response_type=code&client_id={CLIENT_ID}&state={STATE}"
        )
        established_response = http.redirect_response(
            location,
            set_cookies=("__Host-cuevion_auth_tx=opaque; Secure; HttpOnly",),
        )
        token = INVITATION_ID + "." + "a" * 43
        with (
            mock.patch.dict(os.environ, ENVIRONMENT, clear=True),
            mock.patch.object(
                registration_authority.runtime,
                "login_response",
                return_value=established_response,
            ),
            mock.patch.object(
                registration_authority.runtime,
                "_team_authority",
                side_effect=RuntimeError("unavailable"),
            ),
        ):
            response = registration_authority.login_response(
                "GET",
                (("host", "app.cuevion.com"), ("sec-fetch-site", "same-origin")),
                "/api/auth/login?team_invite=" + token,
            )
        self.assertEqual(response.status, 503)
        self.assertFalse(
            any(name.lower() == "set-cookie" for name, _ in response.headers)
        )

    def test_action_validation_consumes_exact_grant_once(self):
        _commands, store = self._store_with_grant()
        first = registration_authority.validation_response(
            "POST",
            self._headers(),
            self._body(),
            environment=ENVIRONMENT,
            now=NOW + 1,
            store_factory=lambda _environment: store,
        )
        self.assertEqual(first.status, 200)
        self.assertEqual(json.loads(first.body), {"authorized": True})

        replay = registration_authority.validation_response(
            "POST",
            self._headers(),
            self._body(),
            environment=ENVIRONMENT,
            now=NOW + 2,
            store_factory=lambda _environment: store,
        )
        self.assertEqual(replay.status, 403)
        self.assertEqual(json.loads(replay.body), {"authorized": False})

    def test_invalid_binding_does_not_consume_valid_grant(self):
        for changes in (
            {"email": "other@example.com"},
            {"clientId": "other-client"},
            {"acrValue": registration_authority.ACR_PREFIX + "H" * 43},
        ):
            with self.subTest(changes=changes):
                _commands, store = self._store_with_grant()
                denied = registration_authority.validation_response(
                    "POST",
                    self._headers(),
                    self._body(**changes),
                    environment=ENVIRONMENT,
                    now=NOW + 1,
                    store_factory=lambda _environment: store,
                )
                self.assertEqual(denied.status, 403)
                valid = registration_authority.validation_response(
                    "POST",
                    self._headers(),
                    self._body(),
                    environment=ENVIRONMENT,
                    now=NOW + 1,
                    store_factory=lambda _environment: store,
                )
                self.assertEqual(valid.status, 200)

    def test_action_validation_is_fail_closed_for_auth_context_and_configuration(self):
        cases = (
            ("GET", self._headers(), ENVIRONMENT, 405),
            (
                "POST",
                self._headers(secret="wrong-secret-" + "x" * 32),
                ENVIRONMENT,
                403,
            ),
            (
                "POST",
                (("host", "evil.example"), *self._headers()[1:]),
                ENVIRONMENT,
                403,
            ),
            (
                "POST",
                tuple(pair for pair in self._headers() if pair[0] != "content-type"),
                ENVIRONMENT,
                415,
            ),
            (
                "POST",
                self._headers(),
                {
                    key: value
                    for key, value in ENVIRONMENT.items()
                    if key != "CUEVION_AUTH0_REGISTRATION_AUTHORITY_SECRET"
                },
                503,
            ),
        )
        for method, headers, environment, expected in cases:
            with self.subTest(method=method, expected=expected):
                _commands, store = self._store_with_grant()
                response = registration_authority.validation_response(
                    method,
                    headers,
                    self._body(),
                    environment=environment,
                    now=NOW + 1,
                    store_factory=lambda _environment: store,
                )
                self.assertEqual(response.status, expected)
                self.assertEqual(json.loads(response.body), {"authorized": False})


if __name__ == "__main__":
    unittest.main()
