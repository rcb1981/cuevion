from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from api.auth import auth0_flow, http, registration_authority, runtime


NOW = 1_800_000_000
CLIENT_ID = "tester-registration-test-client"
TOKEN = "tsti_" + "A" * 22 + "." + "B" * 43
INVITATION_ID = "tsti_" + "A" * 22
TOKEN_DIGEST = "c" * 64
EMAIL = "tester@example.com"
SHARED_SECRET = "tester-registration-authority-" + "x" * 32
ENVIRONMENT = {
    "CUEVION_AUTH0_DOMAIN": auth0_flow.AUTH0_DOMAIN,
    "CUEVION_AUTH0_CLIENT_ID": CLIENT_ID,
    "CUEVION_AUTH0_CLIENT_SECRET": "tester-registration-client-secret",
    "CUEVION_AUTH_SESSION_SECRET": "R" * 48,
    "CUEVION_AUTH0_REGISTRATION_AUTHORITY_SECRET": SHARED_SECRET,
}


class MemoryCommands:
    def __init__(self):
        self.values: dict[str, str] = {}

    def __call__(self, command: list[object]) -> dict[str, object]:
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
            if self.values.get(key) != expected:
                return {"result": 0}
            del self.values[key]
            return {"result": 1}
        raise AssertionError(operation)


class FakeTesterAuthority:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def read_provisioning_invitation(self, token, *, allow_provisioned=False):
        self.calls.append((token, allow_provisioned))
        if token != TOKEN:
            raise ValueError("bad token")
        return SimpleNamespace(
            invitation_id=INVITATION_ID,
            email=EMAIL,
            token_digest=TOKEN_DIGEST,
            expires_at=(NOW + 600) * 1000,
            status="invited",
        )


def tester_headers(body: bytes):
    return (
        ("host", "app.cuevion.com"),
        ("origin", http.CANONICAL_APP_ORIGIN),
        ("sec-fetch-site", "same-origin"),
        ("content-type", "application/x-www-form-urlencoded"),
        ("content-length", str(len(body))),
    )


class TesterRegistrationAuthorityTests(unittest.TestCase):
    def test_source_specific_authority_ids_cannot_cross_namespaces(self):
        valid = registration_authority.RegistrationGrantRecord(
            source="tester_invite",
            email=EMAIL,
            client_id=CLIENT_ID,
            authority_id=INVITATION_ID,
            authority_digest=TOKEN_DIGEST,
            created_at=NOW,
            expires_at=NOW + 60,
        )
        self.assertEqual(valid.source, "tester_invite")

        for source, authority_id in (
            ("tester_invite", "tinv_registration_test"),
            ("team_invite", INVITATION_ID),
        ):
            with self.assertRaises(ValueError):
                registration_authority.RegistrationGrantRecord(
                    source=source,
                    email=EMAIL,
                    client_id=CLIENT_ID,
                    authority_id=authority_id,
                    authority_digest=TOKEN_DIGEST,
                    created_at=NOW,
                    expires_at=NOW + 60,
                )

    def test_auth0_transaction_carries_tester_bearer_only_in_encrypted_transaction(self):
        config = auth0_flow.Auth0Configuration(
            domain=auth0_flow.AUTH0_DOMAIN,
            client_id=CLIENT_ID,
            client_secret="secret",
            session_secret="S" * 48,
        )
        counter = 0

        def random_bytes(size: int) -> bytes:
            nonlocal counter
            counter += 1
            return bytes([counter]) * size

        request = auth0_flow.build_authorization_request(
            config,
            NOW,
            random_bytes=random_bytes,
            tester_invite_token=TOKEN,
        )
        self.assertEqual(request.transaction.tester_invite_token, TOKEN)
        self.assertIsNone(request.transaction.team_invite_token)
        self.assertNotIn(TOKEN, request.authorization_url)
        self.assertNotIn(TOKEN, request.transaction_cookie)

        cookie_pair = request.transaction_cookie.split(";", 1)[0]
        cookie_value = cookie_pair.split("=", 1)[1]
        restored = auth0_flow.decrypt_transaction_cookie(
            cookie_value,
            config,
            NOW,
        )
        self.assertEqual(restored.tester_invite_token, TOKEN)
        self.assertIsNone(restored.team_invite_token)

        with self.assertRaises(auth0_flow.Auth0FlowError):
            auth0_flow.build_authorization_request(
                config,
                NOW,
                random_bytes=random_bytes,
                team_invite_token="tinv_x." + "C" * 43,
                tester_invite_token=TOKEN,
            )

    def test_runtime_login_reproves_tester_before_auth0_redirect(self):
        tester = FakeTesterAuthority()
        response = runtime.login_response(
            "GET",
            (("host", "app.cuevion.com"),),
            None,
            environment=ENVIRONMENT,
            now=NOW,
            tester_invite_token=TOKEN,
            tester_authority_factory=lambda _source: tester,
        )
        self.assertEqual(response.status, 303)
        location = dict(response.headers)["Location"]
        self.assertNotIn(TOKEN, location)
        self.assertEqual(tester.calls, [(TOKEN, True)])

    def test_tester_login_post_exchanges_bearer_for_single_use_registration_grant(self):
        body = urlencode({"tester_invite": TOKEN}).encode("ascii")
        tester = FakeTesterAuthority()
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        auth0_location = (
            auth0_flow.AUTH0_AUTHORIZE_ENDPOINT
            + "?response_type=code"
            + "&client_id=" + CLIENT_ID
            + "&state=" + "S" * 43
        )
        base_response = http.redirect_response(
            auth0_location,
            set_cookies=(
                "__Host-cuevion_auth_tx=opaque; Path=/; Secure; HttpOnly; SameSite=Lax",
            ),
        )

        with mock.patch.object(
            registration_authority.runtime,
            "login_response",
            return_value=base_response,
        ) as login:
            response = registration_authority.tester_login_response(
                "POST",
                tester_headers(body),
                body,
                environment=ENVIRONMENT,
                now=NOW,
                tester_authority_factory=lambda _source: tester,
                store_factory=lambda _source: store,
            )

        self.assertEqual(response.status, 303)
        login.assert_called_once()
        args, kwargs = login.call_args
        self.assertEqual(args[0], "GET")
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["tester_invite_token"], TOKEN)

        location = dict(response.headers)["Location"]
        self.assertNotIn(TOKEN, location)
        query = parse_qs(urlsplit(location).query)
        acr = query["acr_values"][0]
        self.assertTrue(acr.startswith(registration_authority.ACR_PREFIX))
        grant = acr[len(registration_authority.ACR_PREFIX):]
        record = store.get(grant, now=NOW)
        self.assertIsNotNone(record)
        self.assertEqual(record.source, "tester_invite")
        self.assertEqual(record.email, EMAIL)
        self.assertEqual(record.authority_id, INVITATION_ID)
        self.assertEqual(record.authority_digest, TOKEN_DIGEST)
        self.assertEqual(tester.calls, [(TOKEN, True)])

    def test_auth0_action_consumes_tester_source_grant_once(self):
        commands = MemoryCommands()
        store = registration_authority.RegistrationGrantStore(commands)
        grant = registration_authority.issue_tester_invite_grant(
            store,
            email=EMAIL,
            client_id=CLIENT_ID,
            invitation_id=INVITATION_ID,
            invitation_token_digest=TOKEN_DIGEST,
            invitation_expires_at_ms=(NOW + 600) * 1000,
            now=NOW,
            random_bytes=lambda size: b"G" * size,
        )
        acr = registration_authority.ACR_PREFIX + grant
        payload = json.dumps({
            "acrValue": acr,
            "clientId": CLIENT_ID,
            "email": EMAIL,
        }).encode("utf-8")
        headers = (
            ("host", "app.cuevion.com"),
            ("authorization", "Bearer " + SHARED_SECRET),
            ("content-type", "application/json"),
        )
        response = registration_authority.validation_response(
            "POST",
            headers,
            payload,
            environment=ENVIRONMENT,
            now=NOW,
            store_factory=lambda _source: store,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body), {"authorized": True})

        replay = registration_authority.validation_response(
            "POST",
            headers,
            payload,
            environment=ENVIRONMENT,
            now=NOW,
            store_factory=lambda _source: store,
        )
        self.assertEqual(replay.status, 403)

    def test_tester_login_rejects_query_style_and_cross_site_transport(self):
        body = urlencode({"tester_invite": TOKEN}).encode("ascii")
        bad_headers = tuple(
            ("origin", "https://evil.example")
            if name == "origin" else (name, value)
            for name, value in tester_headers(body)
        )
        response = registration_authority.tester_login_response(
            "POST",
            bad_headers,
            body,
            environment=ENVIRONMENT,
            now=NOW,
        )
        self.assertEqual(response.status, 403)

        duplicate_body = (
            "tester_invite=" + TOKEN + "&tester_invite=" + TOKEN
        ).encode("ascii")
        response = registration_authority.tester_login_response(
            "POST",
            tester_headers(duplicate_body),
            duplicate_body,
            environment=ENVIRONMENT,
            now=NOW,
        )
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
