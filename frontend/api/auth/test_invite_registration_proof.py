"""Focused tests for invite-bound Auth0 pre-registration proof transport."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from api.auth import auth0_flow, http, runtime
from api.auth.invite_registration_proof import (
    ACR_PREFIX,
    ENV_NAME,
    MAX_TTL_SECONDS,
    build_invite_registration_acr,
)
from api.auth.invite_registration_redirect import decorate_team_invite_redirect
from api.team.authority import ProvisioningInvitation


NOW = 1_800_000_000
CLIENT_ID = "invite-proof-client"
SECRET = "invite-registration-secret-" + "x" * 32
ENVIRONMENT = {
    "CUEVION_AUTH0_DOMAIN": auth0_flow.AUTH0_DOMAIN,
    "CUEVION_AUTH0_CLIENT_ID": CLIENT_ID,
    "CUEVION_AUTH0_CLIENT_SECRET": "client-secret",
    "CUEVION_AUTH_SESSION_SECRET": "S" * 48,
    ENV_NAME: SECRET,
}
INVITATION_ID = "tinv_" + "A" * 22
RAW_TOKEN = INVITATION_ID + "." + "b" * 43
TOKEN_DIGEST = hashlib.sha256(RAW_TOKEN.encode("ascii")).hexdigest()
EMAIL = "member@example.com"


def _decode_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))


def _header(response, name: str) -> list[str]:
    return [value for key, value in response.headers if key.casefold() == name.casefold()]


class InviteRegistrationProofTests(unittest.TestCase):
    def test_proof_binds_client_email_invitation_digest_and_short_expiry(self):
        proof = build_invite_registration_acr(
            ENVIRONMENT,
            client_id=CLIENT_ID,
            email="  MEMBER@Example.COM  ",
            invitation_id=INVITATION_ID,
            token_digest=TOKEN_DIGEST,
            now=NOW,
            invitation_expires_at_ms=(NOW + 3600) * 1000,
        )
        self.assertTrue(proof.startswith(ACR_PREFIX))
        payload_part, signature_part = proof[len(ACR_PREFIX):].split(".", 1)
        payload_bytes = _decode_part(payload_part)
        payload = json.loads(payload_bytes)
        self.assertEqual(payload, {
            "aud": CLIENT_ID,
            "email": EMAIL,
            "exp": NOW + MAX_TTL_SECONDS,
            "iat": NOW,
            "invitation_id": INVITATION_ID,
            "token_digest": TOKEN_DIGEST,
            "v": 1,
        })
        expected_signature = hmac.new(
            SECRET.encode(), payload_bytes, hashlib.sha256
        ).digest()
        self.assertTrue(hmac.compare_digest(
            expected_signature, _decode_part(signature_part)
        ))
        self.assertNotIn(RAW_TOKEN, proof)

    def test_proof_is_capped_by_invitation_expiry(self):
        proof = build_invite_registration_acr(
            ENVIRONMENT,
            client_id=CLIENT_ID,
            email=EMAIL,
            invitation_id=INVITATION_ID,
            token_digest=TOKEN_DIGEST,
            now=NOW,
            invitation_expires_at_ms=(NOW + 45) * 1000,
        )
        payload_part = proof[len(ACR_PREFIX):].split(".", 1)[0]
        self.assertEqual(json.loads(_decode_part(payload_part))["exp"], NOW + 45)

    def test_missing_weak_or_expired_inputs_fail_closed(self):
        variants = (
            ({**ENVIRONMENT, ENV_NAME: "short"}, EMAIL, (NOW + 60) * 1000),
            ({key: value for key, value in ENVIRONMENT.items() if key != ENV_NAME}, EMAIL, (NOW + 60) * 1000),
            (ENVIRONMENT, "not-an-email", (NOW + 60) * 1000),
            (ENVIRONMENT, EMAIL, NOW * 1000),
        )
        for environment, email, expires in variants:
            with self.subTest(email=email, expires=expires), self.assertRaises(ValueError):
                build_invite_registration_acr(
                    environment,
                    client_id=CLIENT_ID,
                    email=email,
                    invitation_id=INVITATION_ID,
                    token_digest=TOKEN_DIGEST,
                    now=NOW,
                    invitation_expires_at_ms=expires,
                )


class InviteRegistrationRedirectTests(unittest.TestCase):
    def _invite(self):
        return ProvisioningInvitation(
            INVITATION_ID,
            "wsp_" + "A" * 22,
            EMAIL,
            "usr_" + "B" * 21 + "Q",
            TOKEN_DIGEST,
            "invited",
            (NOW + 3600) * 1000,
        )

    def _auth0_response(self, *, extra_query=None):
        pairs = [
            ("response_type", "code"),
            ("client_id", CLIENT_ID),
            ("redirect_uri", auth0_flow.CALLBACK_URI),
            ("connection", auth0_flow.DATABASE_CONNECTION),
        ]
        if extra_query:
            pairs.extend(extra_query)
        return http.redirect_response(
            f"https://{auth0_flow.AUTH0_DOMAIN}/authorize?{urlencode(pairs)}",
            set_cookies=("__Host-cuevion_auth_tx=test; Secure; HttpOnly; SameSite=Lax",),
        )

    def test_valid_invite_adds_one_signed_acr_and_preserves_transaction_cookie(self):
        team = SimpleNamespace(read_provisioning_invitation=lambda token, allow_accepted=False: self._invite())
        response = decorate_team_invite_redirect(
            self._auth0_response(),
            "/api/auth/login?team_invite=" + RAW_TOKEN,
            environment=ENVIRONMENT,
            now=NOW,
            team_authority_factory=lambda _environment: team,
        )
        self.assertEqual(response.status, 303)
        location = _header(response, "location")[0]
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(len(query["acr_values"]), 1)
        self.assertTrue(query["acr_values"][0].startswith(ACR_PREFIX))
        self.assertNotIn(RAW_TOKEN, location)
        self.assertEqual(len(_header(response, "set-cookie")), 1)

    def test_normal_or_non_auth_redirects_are_unchanged(self):
        normal = self._auth0_response()
        self.assertEqual(
            decorate_team_invite_redirect(
                normal, "/api/auth/login", environment=ENVIRONMENT, now=NOW
            ),
            normal,
        )
        local = http.redirect_response("/login")
        self.assertEqual(
            decorate_team_invite_redirect(
                local,
                "/api/auth/login?team_invite=" + RAW_TOKEN,
                environment=ENVIRONMENT,
                now=NOW,
            ),
            local,
        )

    def test_non_redirect_boundary_errors_are_preserved_even_with_malformed_path(self):
        original = http.json_response(400, {"error": {"code": "invalid_request"}})
        self.assertEqual(
            decorate_team_invite_redirect(
                original,
                "/api/auth/login?team_invite=%ZZ",
                environment=ENVIRONMENT,
                now=NOW,
            ),
            original,
        )

    def test_missing_secret_or_existing_acr_fails_closed(self):
        team = SimpleNamespace(read_provisioning_invitation=lambda token, allow_accepted=False: self._invite())
        no_secret = {key: value for key, value in ENVIRONMENT.items() if key != ENV_NAME}
        response = decorate_team_invite_redirect(
            self._auth0_response(),
            "/api/auth/login?team_invite=" + RAW_TOKEN,
            environment=no_secret,
            now=NOW,
            team_authority_factory=lambda _environment: team,
        )
        self.assertEqual(response.status, 503)
        duplicate = decorate_team_invite_redirect(
            self._auth0_response(extra_query=(("acr_values", "foreign"),)),
            "/api/auth/login?team_invite=" + RAW_TOKEN,
            environment=ENVIRONMENT,
            now=NOW,
            team_authority_factory=lambda _environment: team,
        )
        self.assertEqual(duplicate.status, 503)


if __name__ == "__main__":
    unittest.main()
