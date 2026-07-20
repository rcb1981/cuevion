"""Offline security tests for the Auth0 protocol and HTTP primitives."""

from __future__ import annotations

import base64
import io
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import auth0_flow as flow
from . import http


_PRIVATE_KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC3FKGUFmGgzOU6
TOhsACnC/9NglORu1AzGV1GH8BodtGwSTcXop4JZQjRihQBVoCEfClI89vEEbr3p
jFJrPDkOTI0MvrEMkBJY2+L3zMZQQxh5XmFNdw3Z0aioFF3voaK9MlPJkJm2jNiI
J/VZVw3DAQH/Y8I0RpG/7LwxyhtRorU76za+ODD5Dxg3+xKUQ16y/ryuewmKooib
ha4nv5/WVoyjDO5gjwtWyZnetTI4hUCi/AeJTedf1vkkwyD3dFEa/UkEGqIKrfLv
Kz9zlE8x4ukCre/Qv2xQt7QD2nFHbfpnno11I+9hK2pIr/hvRBnnzvLeq/yKtXyV
dMiizwdpAgMBAAECggEAFBGIAi9/YWTgYjcEubT9Xu2TzjgX/7GRGms/Kc1nEXdB
6vI5Rd7Zxh7AlwWRAzHPu6MyH1Jq2kNo4ClUbgKOlwuLYRikl/XRewnUa/kXinwG
WmR++kK5AxXNPOppnx9K014pStSl0tnG0Qr/RR0qqiPpxATbiIctShphSTxoKPci
u77PLew8gjeCLdPE+pThgr6ZQZ36c3jyFkgbQPNoR6DQuhelrdbGRCe/BlqX6bq1
Vl+69x23l1vQxiOvB0iATCnkZSI/zBCNoviUtzYYzNt+v+V2dZfNegKUtm3j8Eic
EErKaoXN53RLA5eZ2MhAbZRyx0uwe9VtR3N6ClQRoQKBgQD+oJmS/tPNa7pHER5H
1XTzUf2sJ3MdhxTJ4pWGwCv86BvkxSoZ6T6/ePSENksC2zrXD+hNanrBN34Jfakf
h2EGt+g7pun1uy6xyiZy/zmGAIa7RKTlwnf92DPV5+c5c7evGVMhMulkqq/LCfwn
777Pv6VfVmQ0Jva3iQQcPITeCQKBgQC4EUrvGEPqBPMgWS1O3mbuWivwNjy6Bg+N
uX81+RQ9n1enm3LKpiYqyl7cwOfKSDitTjWU95RNm5K+zqLVwSUObv7ZamfcgnFz
s4+1IlVJ6xTAsEkt3gmHyZaE7UcGF3ZMaf4xkcf+nLGVgvwMrC+jFP/bVnsq/MaJ
5fMDuNk2YQKBgQC5GtFqLjyVWlpZ7ZTgzcmuVY2fSDKEZb30IfdntW6E9cvJXJgF
rC2Ejo7bSojvc6Zrz9Gl7eF9czT5+1Mma4lak/mM3AO7My936ihXczlDNEC+BOIH
cX8/l5vfRi4u8vO2pCdtvBA1sWwIo6Ke+cfySTUUgL5pt2Wl+UJ2sHw62QKBgAzc
UHMCLASW0fHpqSvAiEqRDE7dS0LoF4AcfNHllE916abxSoT0NOh6eURNSiStBSC+
vSmqXrdJbmhcga4Tr6YhhTblo1oZ1xlxa1IJkxH2Fd4csxA8WkgdgqHI/lRjoUVX
hoYqHGIiypmarEeqZC2t0u6dTT/Ep46M/Xy+FpchAoGBANwXZVF6OtMKK0bRzujG
HwgVheOMIPu6cxHzfHjJQ5A3l6+OfxikB4kT3YdhfxxjjJzoT0H4sUclPEqyt2V5
r1oSzbabt7hjJC1GzfFuHCqMMtQEqYz+/i++IQLuXEn3wKx2GfTUYuntJoIN2Ima
mKxkrJz2kiA63l/ByrYhaDvz
-----END PRIVATE KEY-----
"""

NOW = 1_800_000_000
CLIENT_ID = "synthetic-client-id"
CLIENT_SECRET = "synthetic-client-secret-value"
SESSION_SECRET = "S" * 48
NONCE = base64.urlsafe_b64encode(b"N" * 32).rstrip(b"=").decode("ascii")
KID = "synthetic-key-1"


def _configuration(*, session_secret: str = SESSION_SECRET) -> flow.Auth0Configuration:
    return flow.parse_auth0_configuration(
        {
            "CUEVION_AUTH0_DOMAIN": flow.AUTH0_DOMAIN,
            "CUEVION_AUTH0_CLIENT_ID": CLIENT_ID,
            "CUEVION_AUTH0_CLIENT_SECRET": CLIENT_SECRET,
            "CUEVION_AUTH_SESSION_SECRET": session_secret,
        }
    )


class _FixedRandom:
    def __init__(self) -> None:
        self.values = [b"S" * 32, b"N" * 32, b"V" * 32, b"I" * 12]
        self.counts: list[int] = []

    def __call__(self, count: int) -> bytes:
        self.counts.append(count)
        value = self.values.pop(0)
        if len(value) != count:
            raise AssertionError("unexpected random-byte request")
        return value


def _cookie_value(set_cookie: str) -> str:
    return set_cookie.split(";", 1)[0].split("=", 1)[1]


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


_PRIVATE_KEY = serialization.load_pem_private_key(_PRIVATE_KEY_PEM, password=None)
_PUBLIC_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()


def _jwk(*, kid: str = KID, modulus: int | None = None, alg: str = "RS256") -> dict:
    selected_modulus = _PUBLIC_NUMBERS.n if modulus is None else modulus
    return {
        "alg": alg,
        "e": _b64(_PUBLIC_NUMBERS.e.to_bytes(3, "big")),
        "kid": kid,
        "kty": "RSA",
        "n": _b64(selected_modulus.to_bytes((selected_modulus.bit_length() + 7) // 8, "big")),
        "use": "sig",
    }


def _jwks(*keys: dict) -> bytes:
    selected = list(keys) if keys else [_jwk()]
    return json.dumps({"keys": selected}, separators=(",", ":"), sort_keys=True).encode()


_MISSING = object()


def _claims(**overrides: object) -> dict:
    claims = {
        "aud": CLIENT_ID,
        "email": "member@example.com",
        "email_verified": True,
        "exp": NOW + 3_600,
        "iat": NOW - 60,
        "iss": flow.AUTH0_ISSUER,
        "nonce": NONCE,
        "sub": "auth0|synthetic-subject",
    }
    for name, value in overrides.items():
        if value is _MISSING:
            claims.pop(name, None)
        else:
            claims[name] = value
    return claims


def _token(
    claims: dict | None = None,
    *,
    header: dict | None = None,
) -> str:
    protected = header or {"alg": "RS256", "kid": KID, "typ": "JWT"}
    header_segment = _b64(
        json.dumps(protected, separators=(",", ":"), sort_keys=True).encode()
    )
    payload_segment = _b64(
        json.dumps(claims or _claims(), separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _PRIVATE_KEY.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{payload_segment}.{_b64(signature)}"


class AuthorizationRequestTests(unittest.TestCase):
    def test_exact_authorization_request_pkce_and_cookie_contract(self):
        random_bytes = _FixedRandom()
        result = flow.build_authorization_request(
            _configuration(), NOW, random_bytes=random_bytes
        )
        parsed = urlsplit(result.authorization_url)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, flow.AUTH0_DOMAIN)
        self.assertEqual(parsed.path, "/authorize")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], [CLIENT_ID])
        self.assertEqual(query["redirect_uri"], [flow.CALLBACK_URI])
        self.assertEqual(query["scope"], ["openid profile email"])
        self.assertEqual(query["connection"], ["email"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["prompt"], ["login"])
        self.assertEqual(query["state"], [result.transaction.state])
        self.assertEqual(query["nonce"], [result.transaction.nonce])
        expected_challenge = _b64(
            __import__("hashlib").sha256(
                result.transaction.code_verifier.encode("ascii")
            ).digest()
        )
        self.assertEqual(query["code_challenge"], [expected_challenge])
        self.assertNotIn(CLIENT_SECRET, result.authorization_url)
        self.assertEqual(random_bytes.counts, [32, 32, 32, 12])

        cookie = result.transaction_cookie
        self.assertTrue(cookie.startswith(f"{flow.AUTH_TRANSACTION_COOKIE_NAME}=v1."))
        self.assertIn("; Path=/", cookie)
        self.assertIn("; Max-Age=600", cookie)
        self.assertIn("; Secure", cookie)
        self.assertIn("; HttpOnly", cookie)
        self.assertIn("; SameSite=Lax", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn(result.transaction.state, _cookie_value(cookie))
        self.assertNotIn(result.transaction.nonce, _cookie_value(cookie))
        self.assertNotIn(result.transaction.code_verifier, _cookie_value(cookie))

        decrypted = flow.decrypt_transaction_cookie(
            _cookie_value(cookie), _configuration(), NOW + 1
        )
        self.assertEqual(decrypted, result.transaction)
        consumed = flow.consume_transaction_cookie(
            _cookie_value(cookie), result.transaction.state, _configuration(), NOW + 1
        )
        self.assertEqual(consumed, result.transaction)

    def test_transaction_cookie_rejects_tampering_wrong_key_expiry_and_state(self):
        result = flow.build_authorization_request(
            _configuration(), NOW, random_bytes=_FixedRandom()
        )
        value = _cookie_value(result.transaction_cookie)
        replacement = "A" if value[-1] != "A" else "B"
        cases = (
            (value[:-1] + replacement, _configuration(), NOW + 1, result.transaction.state),
            (value, _configuration(session_secret="W" * 48), NOW + 1, result.transaction.state),
            (value, _configuration(), NOW + 600, result.transaction.state),
            (value, _configuration(), NOW + 1, _b64(b"X" * 32)),
        )
        for cookie, configuration, now, returned_state in cases:
            with self.subTest(now=now, returned_state=returned_state), self.assertRaises(
                flow.Auth0FlowError
            ) as raised:
                flow.consume_transaction_cookie(
                    cookie, returned_state, configuration, now
                )
            self.assertEqual(raised.exception.code, "invalid_transaction")

    def test_clear_cookie_and_configuration_fail_closed(self):
        cleared = flow.clear_transaction_cookie()
        self.assertEqual(
            cleared,
            "__Host-cuevion_auth_tx=; Path=/; Max-Age=0; "
            "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly; SameSite=Lax",
        )
        values = {
            "CUEVION_AUTH0_DOMAIN": "attacker.example",
            "CUEVION_AUTH0_CLIENT_ID": CLIENT_ID,
            "CUEVION_AUTH0_CLIENT_SECRET": CLIENT_SECRET,
            "CUEVION_AUTH_SESSION_SECRET": SESSION_SECRET,
        }
        with self.assertRaises(flow.Auth0FlowError) as raised:
            flow.parse_auth0_configuration(values)
        self.assertEqual(raised.exception.code, "invalid_configuration")
        self.assertNotIn(CLIENT_SECRET, repr(_configuration()))


class TokenExchangeTests(unittest.TestCase):
    def test_token_request_uses_exact_endpoint_body_and_pkce(self):
        verifier = _b64(b"V" * 32)
        request = flow.build_token_exchange_request(
            _configuration(), "synthetic-code", verifier
        )
        self.assertEqual(request.url, flow.AUTH0_TOKEN_ENDPOINT)
        self.assertEqual(request.method, "POST")
        self.assertNotIn(CLIENT_SECRET, request.url)
        payload = parse_qs(request.body.decode("ascii"), strict_parsing=True)
        self.assertEqual(payload["grant_type"], ["authorization_code"])
        self.assertEqual(payload["client_id"], [CLIENT_ID])
        self.assertEqual(payload["client_secret"], [CLIENT_SECRET])
        self.assertEqual(payload["code"], ["synthetic-code"])
        self.assertEqual(payload["redirect_uri"], [flow.CALLBACK_URI])
        self.assertEqual(payload["code_verifier"], [verifier])
        self.assertNotIn(CLIENT_SECRET, repr(request))

    def test_exchange_uses_injected_transport_and_rejects_redirects(self):
        calls: list[flow.OutboundRequest] = []

        def transport(request: flow.OutboundRequest) -> flow.OutboundResponse:
            calls.append(request)
            body = b'{"id_token":"a.b.c","token_type":"Bearer","expires_in":3600}'
            return flow.OutboundResponse(
                200,
                request.url,
                (("Content-Type", "application/json"),),
                body,
            )

        response = flow.exchange_authorization_code(
            _configuration(), "synthetic-code", _b64(b"V" * 32), transport
        )
        self.assertEqual(response.id_token, "a.b.c")
        self.assertEqual(len(calls), 1)

        def redirected(request: flow.OutboundRequest) -> flow.OutboundResponse:
            return flow.OutboundResponse(
                200,
                "https://attacker.example/token",
                (("Content-Type", "application/json"),),
                b'{"id_token":"a.b.c"}',
            )

        with self.assertRaises(flow.Auth0FlowError) as raised:
            flow.exchange_authorization_code(
                _configuration(), "synthetic-code", _b64(b"V" * 32), redirected
            )
        self.assertEqual(raised.exception.code, "provider_unavailable")

        def malformed_headers(
            request: flow.OutboundRequest,
        ) -> flow.OutboundResponse:
            return flow.OutboundResponse(
                200,
                request.url,
                (("Content-Type",),),  # type: ignore[arg-type]
                b'{"id_token":"a.b.c"}',
            )

        with self.assertRaises(flow.Auth0FlowError) as raised:
            flow.exchange_authorization_code(
                _configuration(),
                "synthetic-code",
                _b64(b"V" * 32),
                malformed_headers,
            )
        self.assertEqual(raised.exception.code, "provider_unavailable")

    def test_token_response_is_bounded_strict_and_never_retains_other_tokens(self):
        response = flow.parse_token_response(
            b'{"access_token":"discard-me","id_token":"a.b.c","token_type":"Bearer"}'
        )
        self.assertEqual(set(response.__slots__), {"id_token"})
        self.assertNotIn("discard-me", repr(response))
        for body in (
            b"{}",
            b'{"id_token":"a.b.c","refresh_token":"forbidden"}',
            b'{"id_token":"a.b.c","id_token":"d.e.f"}',
            b"{" + b" " * (256 * 1024) + b"}",
        ):
            with self.subTest(body_length=len(body)), self.assertRaises(
                flow.Auth0FlowError
            ) as raised:
                flow.parse_token_response(body)
            self.assertEqual(raised.exception.code, "invalid_token_response")


class IdTokenValidationTests(unittest.TestCase):
    def test_valid_rs256_token_returns_only_validated_identity_evidence(self):
        evidence = flow.validate_id_token(
            _token(), _jwks(), _configuration(), NONCE, NOW
        )
        self.assertEqual(evidence.issuer, flow.AUTH0_ISSUER)
        self.assertEqual(evidence.subject, "auth0|synthetic-subject")
        self.assertEqual(evidence.email, "member@example.com")
        self.assertEqual(evidence.issued_at, NOW - 60)
        self.assertEqual(evidence.expires_at, NOW + 3_600)
        self.assertEqual(
            set(evidence.__slots__),
            {"issuer", "subject", "email", "issued_at", "expires_at"},
        )
        self.assertNotIn("member@example.com", repr(evidence))

    def test_claim_validation_rejects_every_required_mismatch(self):
        cases = {
            "issuer": _claims(iss="https://attacker.example/"),
            "audience": _claims(aud="other-client"),
            "audience_list": _claims(aud=[CLIENT_ID]),
            "nonce": _claims(nonce=_b64(b"X" * 32)),
            "non_ascii_nonce": _claims(nonce="\N{SNOWMAN}"),
            "expired": _claims(iat=NOW - 100, exp=NOW),
            "future_iat": _claims(iat=NOW + 61, exp=NOW + 3_600),
            "missing_subject": _claims(sub=_MISSING),
            "empty_subject": _claims(sub=""),
            "missing_email": _claims(email=_MISSING),
            "noncanonical_email": _claims(email="Member@example.com"),
            "email_unverified": _claims(email_verified=False),
            "email_verified_integer": _claims(email_verified=1),
            "missing_expiry": _claims(exp=_MISSING),
            "boolean_iat": _claims(iat=True),
            "future_nbf": _claims(nbf=NOW + 61),
            "wrong_azp": _claims(azp="other-client"),
        }
        for name, claims in cases.items():
            with self.subTest(name=name), self.assertRaises(flow.Auth0FlowError) as raised:
                flow.validate_id_token(
                    _token(claims), _jwks(), _configuration(), NONCE, NOW
                )
            self.assertEqual(raised.exception.code, "invalid_id_token")

    def test_header_signature_and_jwks_key_selection_fail_closed(self):
        unsupported = _token(header={"alg": "HS256", "kid": KID, "typ": "JWT"})
        missing_kid = _token(header={"alg": "RS256", "typ": "JWT"})
        valid = _token()
        tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")
        wrong_modulus = _jwks(_jwk(modulus=_PUBLIC_NUMBERS.n + 2))
        cases = (
            (unsupported, _jwks(), "invalid_id_token"),
            (missing_kid, _jwks(), "invalid_id_token"),
            (tampered, _jwks(), "invalid_id_token"),
            (valid, _jwks(_jwk(kid="other")), "invalid_jwks"),
            (valid, _jwks(_jwk(), _jwk()), "invalid_jwks"),
            (valid, _jwks(_jwk(alg="RS512")), "invalid_jwks"),
            (valid, wrong_modulus, "invalid_id_token"),
        )
        for token, jwks, expected_code in cases:
            with self.subTest(expected_code=expected_code), self.assertRaises(
                flow.Auth0FlowError
            ) as raised:
                flow.validate_id_token(token, jwks, _configuration(), NONCE, NOW)
            self.assertEqual(raised.exception.code, expected_code)

    def test_jwks_transport_is_exact_bounded_and_injected(self):
        calls: list[flow.OutboundRequest] = []

        def transport(request: flow.OutboundRequest) -> flow.OutboundResponse:
            calls.append(request)
            return flow.OutboundResponse(
                200,
                request.url,
                (("Content-Type", "application/json; charset=utf-8"),),
                _jwks(),
            )

        evidence = flow.validate_id_token_with_jwks(
            _token(), _configuration(), NONCE, NOW, transport
        )
        self.assertEqual(evidence.subject, "auth0|synthetic-subject")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url, flow.AUTH0_JWKS_ENDPOINT)
        self.assertEqual(calls[0].method, "GET")
        self.assertIsNone(calls[0].body)


class HttpBoundaryTests(unittest.TestCase):
    def test_host_origin_cookie_and_method_are_exact(self):
        headers = (
            ("Host", http.CANONICAL_APP_HOST),
            ("X-Forwarded-Host", http.CANONICAL_APP_HOST),
            ("Origin", http.CANONICAL_APP_ORIGIN),
            ("Cookie", "unknown=value; __Host-cuevion_auth_tx=opaque"),
        )
        self.assertEqual(http.require_method("POST", "POST"), "POST")
        self.assertEqual(http.require_canonical_host(headers), http.CANONICAL_APP_HOST)
        self.assertEqual(http.require_same_origin(headers), http.CANONICAL_APP_ORIGIN)
        self.assertEqual(http.read_cookie(headers, flow.AUTH_TRANSACTION_COOKIE_NAME), "opaque")

        rejected_headers = (
            (("Host", "app.cuevion.com.attacker.example"),),
            (("Host", http.CANONICAL_APP_HOST), ("Host", http.CANONICAL_APP_HOST)),
            (("Origin", "https://app.cuevion.com/"),),
            (("Cookie", "name=one; name=two"),),
        )
        for rejected in rejected_headers:
            with self.subTest(rejected=rejected), self.assertRaises(http.HttpBoundaryError):
                if rejected[0][0] == "Origin":
                    http.require_same_origin(rejected)
                elif rejected[0][0] == "Cookie":
                    http.read_cookie(rejected, "name")
                else:
                    http.require_canonical_host(rejected)

    def test_response_emission_has_only_fixed_security_headers(self):
        response = http.json_response(
            401,
            {"authenticated": False},
            set_cookies=(flow.clear_transaction_cookie(),),
        )

        class Handler:
            def __init__(self):
                self.status = None
                self.headers: list[tuple[str, str]] = []
                self.wfile = io.BytesIO()

            def send_response_only(self, status):
                self.status = status

            def send_header(self, name, value):
                self.headers.append((name, value))

            def end_headers(self):
                return None

        handler = Handler()
        http.send_public_response(handler, response)
        self.assertEqual(handler.status, 401)
        self.assertEqual(handler.wfile.getvalue(), response.body)
        self.assertIn(("Cache-Control", "no-store"), handler.headers)
        self.assertIn(("X-Content-Type-Options", "nosniff"), handler.headers)
        self.assertIn(("Referrer-Policy", "no-referrer"), handler.headers)
        self.assertFalse(any(name.lower().startswith("access-control") for name, _ in handler.headers))

        head_handler = Handler()
        head_handler.command = "HEAD"
        http.send_public_response(head_handler, response)
        self.assertEqual(head_handler.wfile.getvalue(), b"")
        self.assertIn(("Content-Length", str(len(response.body))), head_handler.headers)

        raw_request = SimpleNamespace(
            headers=SimpleNamespace(raw_items=lambda: [("Host", http.CANONICAL_APP_HOST)])
        )
        self.assertEqual(
            http.snapshot_request_headers(raw_request),
            (("Host", http.CANONICAL_APP_HOST),),
        )


if __name__ == "__main__":
    unittest.main()
