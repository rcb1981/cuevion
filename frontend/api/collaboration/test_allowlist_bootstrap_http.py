from __future__ import annotations

import base64
import hashlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from . import (
    allowlist_bootstrap_http as bootstrap,
    authorization,
    http_adapter,
    owner_request_security,
)
from .owner_request_security import (
    OwnerSecurityError,
    VerifiedOwnerAuthentication,
    resolve_owner_request_context,
)


NOW = 1_800_000_000
ORIGIN = "https://app.cuevion.com"
HOST = "app.cuevion.com"
ISSUER = "https://cuevion.eu.auth0.com/"
SUBJECT = "auth0|bootstrap-synthetic-owner"
OWNER_EMAIL = "owner@example.test"
WORKSPACE_ID = "wsp_" + ("w" * 22)
MAILBOX_ID = "synthetic.mailbox-1"
TOKEN_BYTES = b"synthetic-bootstrap-token-material"
HMAC_KEY_BYTES = b"synthetic-allowlist-hmac-material"
SESSION_ID = base64.urlsafe_b64encode(b"s" * 32).rstrip(b"=").decode("ascii")
CREDENTIAL_DIGEST = base64.urlsafe_b64encode(
    hashlib.sha256(b"synthetic-binding").digest()
).rstrip(b"=").decode("ascii")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


TOKEN = _b64(TOKEN_BYTES)
HMAC_KEY = _b64(HMAC_KEY_BYTES)


class _SyntheticMember:
    def __init__(
        self,
        *,
        user_id: str,
        email: str,
        name: str,
        workspace_id: str,
        membership_role: str,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.name = name
        self.workspace_id = workspace_id
        self.membership_role = membership_role
        self.user_type = "member"
        self.auth_source = "auth0"


def _environment(**updates: str) -> dict[str, str]:
    return {
        bootstrap.VERCEL_ENVIRONMENT_NAME: "production",
        bootstrap.APP_ORIGIN_ENVIRONMENT_NAME: ORIGIN,
        bootstrap.BOOTSTRAP_TOKEN_ENVIRONMENT_NAME: TOKEN,
        bootstrap.ALLOWLIST_HMAC_KEY_ENVIRONMENT_NAME: HMAC_KEY,
        **updates,
    }


def _context():
    claims = VerifiedOwnerAuthentication(
        issuer=ISSUER,
        authentication_version=1,
        subject=SUBJECT,
        owner_email=OWNER_EMAIL,
        workspace_id=WORKSPACE_ID,
        display_name="Synthetic Owner",
        session_id=SESSION_ID,
        credential_digest=CREDENTIAL_DIGEST,
        issued_at=NOW - 60,
        expires_at=NOW + 3600,
    )
    return resolve_owner_request_context(
        (),
        authentication_resolver=lambda _headers: claims,
        now=NOW,
    )


def _member(**updates: object) -> _SyntheticMember:
    values: dict[str, object] = {
        "user_id": "usr_" + ("u" * 22),
        "email": OWNER_EMAIL,
        "name": "Synthetic Owner",
        "workspace_id": WORKSPACE_ID,
        "membership_role": "owner",
    }
    values.update(updates)
    return _SyntheticMember(**values)  # type: ignore[arg-type]


def _mailbox_result(
    *,
    mailbox_id: str = MAILBOX_ID,
    member: _SyntheticMember | None = None,
    provider: str = "google",
) -> dict[str, object]:
    trusted_member = _member() if member is None else member
    return {
        "status": "ok",
        "memberAuthority": trusted_member,
        "user": {
            "email": trusted_member.email,
            "name": trusted_member.name,
            "userType": trusted_member.user_type,
        },
        "inbox": {
            "id": mailbox_id,
            "provider": provider,
            "email": "mailbox@example.test",
        },
        "config": {"untrustedRawAuthority": "must-not-escape"},
        "error": None,
    }


class _Headers:
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.pairs = pairs

    def raw_items(self):
        return iter(self.pairs)


class _GuardedReader:
    def read(self, _size: int) -> bytes:
        raise AssertionError("request body must not be read")


class _Request:
    def __init__(
        self,
        payload: bytes,
        *,
        method: str = "POST",
        headers: list[tuple[str, str]] | None = None,
        guarded_reader: bool = False,
        path: str = "/api/collaboration/allowlist_bootstrap",
    ) -> None:
        self.command = method
        self.path = path
        self.headers = _Headers(
            headers
            if headers is not None
            else [
                ("Host", HOST),
                ("Origin", ORIGIN),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
                ("Cookie", "__Host-cuevion_session=synthetic-browser-cookie"),
                ("X-Cuevion-Allowlist-Bootstrap", TOKEN),
            ]
        )
        self.rfile = _GuardedReader() if guarded_reader else io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.response_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        return


def _payload(value: object = MAILBOX_ID, **extra: object) -> bytes:
    return json.dumps(
        {"mailboxId": value, **extra},
        separators=(",", ":"),
    ).encode("utf-8")


def _invoke(
    request: _Request,
    *,
    mode: str = bootstrap.BOOTSTRAP_HTTP_MODE,
    environment: dict[str, str] | None = None,
) -> http_adapter.PublicResponse:
    return http_adapter.invoke_safely(
        lambda: bootstrap.allowlist_bootstrap_response(
            request,
            http_mode=mode,
            environment=_environment() if environment is None else environment,
            now=NOW,
        ),
        allow_method="POST",
    )


class AllowlistBootstrapBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()
        self.real_resolve_context = bootstrap._resolve_context
        self.context_patch = mock.patch.object(
            bootstrap,
            "_resolve_context",
            return_value=self.context,
        )
        self.mailbox_patch = mock.patch.object(
            authorization,
            "_resolve_verified_owned_managed_inbox_record",
            return_value=_mailbox_result(),
        )
        self.resolve_context = self.context_patch.start()
        self.resolve_mailbox = self.mailbox_patch.start()
        original_import = bootstrap.importlib.import_module

        def synthetic_auth_runtime(name: str):
            if name == "api.auth.runtime":
                return SimpleNamespace(AuthenticatedMemberContext=_SyntheticMember)
            return original_import(name)

        self.import_patch = mock.patch.object(
            bootstrap.importlib,
            "import_module",
            side_effect=synthetic_auth_runtime,
        )
        self.import_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.addCleanup(self.mailbox_patch.stop)
        self.addCleanup(self.import_patch.stop)

    def test_context_resolution_reuses_exact_verified_auth0_owner_adapter(self):
        claims = VerifiedOwnerAuthentication(
            issuer=ISSUER,
            authentication_version=1,
            subject=SUBJECT,
            owner_email=OWNER_EMAIL,
            workspace_id=WORKSPACE_ID,
            display_name="Synthetic Owner",
            session_id=SESSION_ID,
            credential_digest=CREDENTIAL_DIGEST,
            issued_at=NOW - 60,
            expires_at=NOW + 3600,
        )
        headers = (("cookie", "opaque-synthetic-session"),)
        environment = _environment()
        with mock.patch.object(
            bootstrap,
            "resolve_verified_auth0_owner",
            return_value=claims,
        ) as resolver:
            context = self.real_resolve_context(
                headers,
                environment=environment,
                now=NOW,
            )
        resolver.assert_called_once_with(
            headers,
            environment=environment,
            now=NOW,
        )
        self.assertEqual(context.owner_email, OWNER_EMAIL)
        self.assertEqual(context.workspace_id, WORKSPACE_ID)

    def test_only_exact_bootstrap_mode_activates_service(self):
        for mode in ("off", "owner_read", "owner_write", "guest", "frontend", ""):
            with self.subTest(mode=mode):
                request = _Request(b"", guarded_reader=True)
                response = _invoke(request, mode=mode)
                self.assertEqual(response.status, 404)
        self.resolve_context.assert_not_called()
        self.resolve_mailbox.assert_not_called()

    def test_nonproduction_runtime_is_not_found_before_headers_or_body(self):
        for runtime_environment in (None, "preview", "development", "Production"):
            with self.subTest(runtime_environment=runtime_environment):
                environment = _environment()
                if runtime_environment is None:
                    environment.pop(bootstrap.VERCEL_ENVIRONMENT_NAME)
                else:
                    environment[bootstrap.VERCEL_ENVIRONMENT_NAME] = (
                        runtime_environment
                    )
                request = _Request(_payload(), guarded_reader=True)
                request.headers = SimpleNamespace(
                    raw_items=lambda: self.fail("nonproduction headers read")
                )
                response = _invoke(
                    request,
                    environment=environment,
                )
                self.assertEqual(response.status, 404)
        self.resolve_context.assert_not_called()
        self.resolve_mailbox.assert_not_called()

    def test_every_non_post_method_is_denied_before_body_read(self):
        for method in (
            "GET",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
            "HEAD",
            "TRACE",
            "CONNECT",
        ):
            with self.subTest(method=method):
                response = _invoke(
                    _Request(b"", method=method, guarded_reader=True)
                )
                self.assertEqual(response.status, 405)
                self.assertIn(("Allow", "POST"), response.headers)
        self.resolve_context.assert_not_called()

    def test_host_and_origin_are_exact_duplicate_safe_boundaries(self):
        valid = _Request(_payload())
        base = list(valid.headers.pairs)
        cases = (
            [pair for pair in base if pair[0].lower() != "host"],
            [("Host", "evil.example") if name.lower() == "host" else (name, value) for name, value in base],
            [("Host", HOST), *base],
            [pair for pair in base if pair[0].lower() != "origin"],
            [("Origin", "https://evil.example") if name.lower() == "origin" else (name, value) for name, value in base],
            [("Origin", ORIGIN), *base],
            [("X-Forwarded-Host", "evil.example"), *base],
        )
        for headers in cases:
            with self.subTest(headers=headers[:2]):
                response = _invoke(
                    _Request(_payload(), headers=headers, guarded_reader=True)
                )
                self.assertIn(response.status, {400, 403})
        self.resolve_context.assert_not_called()

    def test_missing_malformed_wrong_and_duplicate_tokens_are_one_fixed_not_found(self):
        base = _Request(_payload()).headers.pairs
        token_name = "x-cuevion-allowlist-bootstrap"
        without = [pair for pair in base if pair[0].lower() != token_name]
        cases = (
            without,
            [*without, ("X-Cuevion-Allowlist-Bootstrap", "not-base64url!")],
            [*without, ("X-Cuevion-Allowlist-Bootstrap", _b64(b"w" * 32))],
            [*base, ("x-cuevion-allowlist-bootstrap", TOKEN)],
        )
        bodies = []
        for headers in cases:
            response = _invoke(
                _Request(_payload(), headers=headers, guarded_reader=True)
            )
            self.assertEqual(response.status, 404)
            bodies.append(response.body)
        self.assertEqual(len(set(bodies)), 1)
        self.assertTrue(all(TOKEN.encode("ascii") not in body for body in bodies))
        self.resolve_context.assert_not_called()

    def test_token_is_not_accepted_from_query_or_body(self):
        headers = [
            pair
            for pair in _Request(_payload()).headers.pairs
            if pair[0].lower() != bootstrap.BOOTSTRAP_TOKEN_HEADER_NAME
        ]
        query = _Request(
            _payload(),
            headers=headers,
            guarded_reader=True,
            path=f"/api/collaboration/allowlist_bootstrap?token={TOKEN}",
        )
        self.assertEqual(_invoke(query).status, 404)

        body = json.dumps(
            {"mailboxId": MAILBOX_ID, "token": TOKEN},
            separators=(",", ":"),
        ).encode("ascii")
        self.assertEqual(_invoke(_Request(body)).status, 400)
        self.resolve_context.assert_not_called()

    def test_body_is_bounded_strict_and_exactly_one_mailbox_selector(self):
        bodies = (
            b"{}",
            b'{"mailboxId":"one","mailboxId":"two"}',
            b'{"mailboxId":["one"]}',
            b'{"mailboxId":"*"}',
            b'{"mailboxId":"owner@example.test"}',
            b'{"mailboxId":"synthetic.mailbox-1","extra":true}',
            b"{",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(_invoke(_Request(body)).status, 400)
        oversized = b"{" + (b"x" * bootstrap.MAX_BOOTSTRAP_REQUEST_BYTES) + b"}"
        request = _Request(oversized, guarded_reader=True)
        self.assertEqual(_invoke(request).status, 413)
        self.resolve_context.assert_not_called()

    def test_configuration_is_default_closed_and_secrets_must_be_independent(self):
        missing_token = _environment()
        missing_token.pop(bootstrap.BOOTSTRAP_TOKEN_ENVIRONMENT_NAME)
        self.assertEqual(
            _invoke(_Request(_payload(), guarded_reader=True), environment=missing_token).status,
            404,
        )

        for name in (
            bootstrap.APP_ORIGIN_ENVIRONMENT_NAME,
            bootstrap.ALLOWLIST_HMAC_KEY_ENVIRONMENT_NAME,
        ):
            with self.subTest(name=name):
                environment = _environment()
                environment.pop(name)
                response = _invoke(
                    _Request(_payload(), guarded_reader=True),
                    environment=environment,
                )
                self.assertEqual(response.status, 503)

        shared = _environment(
            CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY=TOKEN,
        )
        self.assertEqual(
            _invoke(
                _Request(_payload(), guarded_reader=True),
                environment=shared,
            ).status,
            503,
        )
        self.resolve_context.assert_not_called()

    def test_authentication_failure_families_are_fixed_and_mailbox_is_not_read(self):
        for label, reason, status in (
            ("unauthenticated", "authentication_required", 401),
            ("stale_or_revoked", "authentication_required", 401),
            ("wrong_auth_source", "authentication_required", 401),
            ("session_store_unavailable", "authentication_unavailable", 503),
            ("account_authority_unavailable", "authentication_unavailable", 503),
        ):
            with self.subTest(label=label):
                self.resolve_context.reset_mock(
                    side_effect=True,
                    return_value=True,
                )
                self.resolve_context.side_effect = OwnerSecurityError(reason)
                response = _invoke(_Request(_payload()))
                self.assertEqual(response.status, status)
                self.assertNotIn(OWNER_EMAIL.encode(), response.body)
        self.resolve_mailbox.assert_not_called()

    def test_mailbox_failures_are_closed_and_redacted(self):
        for result, status in (
            ({"status": "not_found", "mailboxSecret": "raw"}, 404),
            ({"status": "unauthorized", "mailboxSecret": "raw"}, 401),
            ({"status": "unavailable", "mailboxSecret": "raw"}, 503),
            ({"status": "malformed", "mailboxSecret": "raw"}, 503),
            ({"status": "conflict", "mailboxSecret": "raw"}, 503),
            (None, 503),
        ):
            with self.subTest(result=result, status=status):
                self.resolve_mailbox.return_value = result
                response = _invoke(_Request(_payload()))
                self.assertEqual(response.status, status)
                self.assertNotIn(b"mailboxSecret", response.body)

    def test_authority_mismatch_wrong_mailbox_and_provider_are_not_found(self):
        cases = (
            _mailbox_result(member=_member(name="Different Owner")),
            _mailbox_result(mailbox_id="other.mailbox"),
            _mailbox_result(provider="unsupported"),
            {
                **_mailbox_result(),
                "user": {"email": "other@example.test"},
            },
        )
        for result in cases:
            with self.subTest(result=result):
                self.resolve_mailbox.return_value = result
                self.assertEqual(_invoke(_Request(_payload())).status, 404)

    def test_valid_authority_returns_only_exact_canonical_digests_and_counts(self):
        request = _Request(_payload())
        response = _invoke(request)
        self.assertEqual(response.status, 200)
        value = json.loads(response.body)
        expected_owner = owner_request_security.derive_owner_allowlist_entry(
            HMAC_KEY_BYTES,
            ISSUER,
            1,
            SUBJECT,
        )
        expected_mailbox = owner_request_security.derive_mailbox_allowlist_entry(
            HMAC_KEY_BYTES,
            ISSUER,
            1,
            SUBJECT,
            MAILBOX_ID,
        )
        self.assertEqual(
            value,
            {
                "owners": 1,
                "mailboxes": 1,
                "ownerDigests": 1,
                "mailboxDigests": 1,
                "ownerAllowlist": expected_owner,
                "mailboxAllowlist": expected_mailbox,
            },
        )
        forbidden = (
            ISSUER,
            SUBJECT,
            OWNER_EMAIL,
            WORKSPACE_ID,
            SESSION_ID,
            MAILBOX_ID,
            TOKEN,
            HMAC_KEY,
            "synthetic-browser-cookie",
            "untrustedRawAuthority",
        )
        self.assertTrue(
            all(item.encode("utf-8") not in response.body for item in forbidden)
        )
        self.resolve_mailbox.assert_called_once_with(
            tuple(request.headers.pairs),
            MAILBOX_ID,
        )

    def test_success_path_never_imports_collaboration_business_mutations(self):
        original_import = bootstrap.importlib.import_module

        def guarded_import(name: str):
            if name in {
                "api.collaboration.application",
                "api.collaboration.mutations",
            }:
                raise AssertionError("business mutation graph imported")
            return original_import(name)

        with mock.patch.object(
            bootstrap.importlib,
            "import_module",
            side_effect=guarded_import,
        ):
            self.assertEqual(_invoke(_Request(_payload())).status, 200)


class AllowlistBootstrapRouteContainmentTests(unittest.TestCase):
    def test_only_route_module_exposes_handler(self):
        from . import allowlist_bootstrap as route

        self.assertTrue(hasattr(route, "handler"))
        self.assertFalse(any(name.lower() == "handler" for name in vars(bootstrap)))

    def test_route_is_default_closed_in_every_other_mode_without_service_import(self):
        from . import allowlist_bootstrap as route

        for mode in (None, "owner_read", "owner_write", "guest", "frontend"):
            with self.subTest(mode=mode):
                request = _Request(b"", guarded_reader=True)
                environment = (
                    {}
                    if mode is None
                    else {
                        http_adapter.HTTP_MODE_ENVIRONMENT_NAME: mode,
                        "VERCEL_ENV": "production",
                    }
                )
                with mock.patch.dict(
                    route.os.environ,
                    environment,
                    clear=True,
                ), mock.patch.object(
                    route.importlib,
                    "import_module",
                    side_effect=AssertionError("inactive route imported service"),
                ):
                    route.handler._respond(request)
                self.assertEqual(request.status, 404)

    def test_nonproduction_exact_mode_does_not_import_bootstrap_service(self):
        from . import allowlist_bootstrap as route

        request = _Request(b"", guarded_reader=True)
        with mock.patch.dict(
            route.os.environ,
            {
                http_adapter.HTTP_MODE_ENVIRONMENT_NAME: bootstrap.BOOTSTRAP_HTTP_MODE,
                "VERCEL_ENV": "preview",
            },
            clear=True,
        ), mock.patch.object(
            route.importlib,
            "import_module",
            side_effect=AssertionError("nonproduction route imported service"),
        ):
            route.handler._respond(request)
        self.assertEqual(request.status, 404)

    def test_exact_mode_activates_only_bootstrap_route(self):
        from . import allowlist_bootstrap as route
        from . import owner

        owner_request = _Request(b"", guarded_reader=True)
        with mock.patch.dict(
            owner.os.environ,
            {
                http_adapter.HTTP_MODE_ENVIRONMENT_NAME: bootstrap.BOOTSTRAP_HTTP_MODE,
                "VERCEL_ENV": "production",
            },
            clear=True,
        ), mock.patch.object(
            owner.importlib,
            "import_module",
            side_effect=AssertionError("bootstrap mode imported owner service"),
        ):
            owner.handler._respond(owner_request)
        self.assertEqual(owner_request.status, 404)

        route_request = _Request(b"", method="GET", guarded_reader=True)
        with mock.patch.dict(
            route.os.environ,
            {
                http_adapter.HTTP_MODE_ENVIRONMENT_NAME: bootstrap.BOOTSTRAP_HTTP_MODE,
                "VERCEL_ENV": "production",
            },
            clear=True,
        ):
            route.handler._respond(route_request)
        self.assertEqual(route_request.status, 405)

    def test_route_logging_is_suppressed(self):
        from . import allowlist_bootstrap as route

        self.assertIsNone(
            route.handler.log_message(SimpleNamespace(), TOKEN, HMAC_KEY)
        )


if __name__ == "__main__":
    unittest.main()
