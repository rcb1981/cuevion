from __future__ import annotations

import base64
import hashlib
import io
import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from . import http_adapter, owner_write_readiness_http as readiness, redis_store


ORIGIN = "https://app.cuevion.com"
HOST = "app.cuevion.com"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _environment(**updates: str) -> dict[str, str]:
    values = {
        readiness.VERCEL_ENVIRONMENT_NAME: "production",
        readiness.GLOBAL_HTTP_MODE_ENVIRONMENT_NAME: readiness.OWNER_READ_MODE,
        readiness.READINESS_MODE_ENVIRONMENT_NAME: readiness.READINESS_MODE,
        readiness.READINESS_TOKEN_ENVIRONMENT_NAME: _b64(b"t" * 32),
        readiness.APP_ORIGIN_ENVIRONMENT_NAME: ORIGIN,
        readiness.SESSION_SECRET_ENVIRONMENT_NAME: "s" * 32,
        readiness.CSRF_KEY_ENVIRONMENT_NAME: _b64(b"c" * 32),
        readiness.ALLOWLIST_KEY_ENVIRONMENT_NAME: _b64(b"a" * 32),
        readiness.OWNER_ALLOWLIST_ENVIRONMENT_NAME: "v1_" + _b64(b"o" * 32),
        readiness.MAILBOX_ALLOWLIST_ENVIRONMENT_NAME: "v1_" + _b64(b"b" * 32),
        readiness.RATE_LIMIT_KEY_ENVIRONMENT_NAME: _b64(b"r" * 32),
        readiness.INDEX_KEY_ENVIRONMENT_NAME: _b64(b"i" * 32),
    }
    values.update(updates)
    return values


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
        payload: bytes = b'{"operation":"verify"}',
        *,
        headers: list[tuple[str, str]] | None = None,
        method: str = "POST",
        guarded_reader: bool = False,
    ) -> None:
        token = _environment()[readiness.READINESS_TOKEN_ENVIRONMENT_NAME]
        self.command = method
        self.headers = _Headers(
            headers
            if headers is not None
            else [
                ("Host", HOST),
                ("Origin", ORIGIN),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
                ("X-Cuevion-Owner-Write-Readiness", token),
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


def _invoke(
    request: _Request,
    *,
    environment: dict[str, str] | None = None,
) -> http_adapter.PublicResponse:
    return http_adapter.invoke_safely(
        lambda: readiness.owner_write_readiness_response(
            request,
            environment=_environment() if environment is None else environment,
        ),
        allow_method="POST",
    )


def _json(response: http_adapter.PublicResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


class OwnerWriteReadinessBoundaryTests(unittest.TestCase):
    def test_inactive_nonproduction_and_non_owner_read_are_not_found_before_input(self):
        cases = (
            {readiness.VERCEL_ENVIRONMENT_NAME: "preview"},
            {readiness.READINESS_MODE_ENVIRONMENT_NAME: "off"},
            {readiness.GLOBAL_HTTP_MODE_ENVIRONMENT_NAME: "owner_write"},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                environment = _environment(**updates)
                request = _Request(guarded_reader=True)
                request.headers = SimpleNamespace(
                    raw_items=lambda: self.fail("inactive request read headers")
                )
                self.assertEqual(_invoke(request, environment=environment).status, 404)

        for name in (
            readiness.VERCEL_ENVIRONMENT_NAME,
            readiness.READINESS_MODE_ENVIRONMENT_NAME,
            readiness.GLOBAL_HTTP_MODE_ENVIRONMENT_NAME,
        ):
            with self.subTest(missing=name):
                environment = _environment()
                environment.pop(name)
                self.assertEqual(
                    _invoke(_Request(guarded_reader=True), environment=environment).status,
                    404,
                )

    def test_active_route_accepts_only_post(self):
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
                response = _invoke(_Request(method=method, guarded_reader=True))
                self.assertEqual(response.status, 405)
                self.assertIn(("Allow", "POST"), response.headers)

    def test_missing_malformed_wrong_and_duplicate_token_are_fixed_not_found(self):
        base = _Request().headers.pairs
        name = readiness.READINESS_TOKEN_HEADER_NAME
        without = [pair for pair in base if pair[0].lower() != name]
        cases = (
            without,
            [*without, ("X-Cuevion-Owner-Write-Readiness", "not-base64url!")],
            [*without, ("X-Cuevion-Owner-Write-Readiness", _b64(b"w" * 32))],
            [*base, ("x-cuevion-owner-write-readiness", _b64(b"t" * 32))],
        )
        bodies = []
        for headers in cases:
            response = _invoke(_Request(headers=headers, guarded_reader=True))
            self.assertEqual(response.status, 404)
            bodies.append(response.body)
        self.assertEqual(len(set(bodies)), 1)

    def test_body_and_same_origin_boundaries_are_closed(self):
        base_headers = _Request().headers.pairs
        invalid_boundaries = (
            [pair for pair in base_headers if pair[0].lower() != "host"],
            [
                ("Host", "evil.example") if name.lower() == "host" else (name, value)
                for name, value in base_headers
            ],
            [("Host", HOST), *base_headers],
            [pair for pair in base_headers if pair[0].lower() != "origin"],
            [
                ("Origin", "https://evil.example")
                if name.lower() == "origin"
                else (name, value)
                for name, value in base_headers
            ],
            [("Origin", ORIGIN), *base_headers],
            [("X-Forwarded-Host", "evil.example"), *base_headers],
        )
        for headers in invalid_boundaries:
            with self.subTest(headers=headers[:2]):
                response = _invoke(
                    _Request(headers=headers, guarded_reader=True)
                )
                self.assertIn(response.status, {400, 403})

        for body in (
            b"{}",
            b'{"operation":"other"}',
            b'{"operation":"verify","extra":true}',
            b'{"operation":"verify","operation":"verify"}',
            b"{",
        ):
            with self.subTest(body=body):
                self.assertEqual(_invoke(_Request(body)).status, 400)

        oversized = b"{" + (b"x" * readiness.MAX_READINESS_REQUEST_BYTES) + b"}"
        self.assertEqual(
            _invoke(_Request(oversized, guarded_reader=True)).status,
            413,
        )

    def test_success_is_only_three_coarse_booleans_and_performs_no_redis_command(self):
        environment = _environment(
            **{
                readiness.CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME: _b64(b"p" * 32),
                readiness.INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME: _b64(b"j" * 32),
                readiness.MAILBOX_SECRET_ENVIRONMENT_NAME: base64.urlsafe_b64encode(
                    b"m" * 32
                ).decode("ascii"),
            }
        )
        original_import = importlib.import_module

        def guarded_import(name: str):
            if name in {
                "api.collaboration.application",
                "api.collaboration.mutations",
            }:
                raise AssertionError("readiness imported business mutation graph")
            return original_import(name)

        with mock.patch.object(
            redis_store,
            "_v2_command",
            side_effect=AssertionError("readiness must not access Redis"),
        ) as redis_command, mock.patch.object(
            redis_store,
            "_v2_eval",
            side_effect=AssertionError("readiness must not evaluate Redis Lua"),
        ) as redis_eval, mock.patch.object(
            readiness.importlib,
            "import_module",
            side_effect=guarded_import,
        ):
            response = _invoke(_Request(), environment=environment)
        redis_command.assert_not_called()
        redis_eval.assert_not_called()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            _json(response),
            {
                "ok": True,
                "data": {
                    "ownerWriteConfigurationValid": True,
                    "requiredSecretsPresent": True,
                    "secretSeparationValid": True,
                },
            },
        )
        forbidden = tuple(environment.values())
        forbidden_hashes = tuple(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in environment.values()
        )
        self.assertTrue(
            all(value.encode("utf-8") not in response.body for value in forbidden)
        )
        self.assertTrue(
            all(value.encode("ascii") not in response.body for value in forbidden_hashes)
        )
        self.assertTrue(
            all(type(value) is bool for value in _json(response)["data"].values())
        )

    def test_optional_rotation_and_mailbox_secrets_may_be_absent(self):
        self.assertEqual(_invoke(_Request()).status, 200)

    def test_every_required_secret_missing_or_malformed_fails_closed(self):
        required = (
            readiness.READINESS_TOKEN_ENVIRONMENT_NAME,
            readiness.SESSION_SECRET_ENVIRONMENT_NAME,
            readiness.CSRF_KEY_ENVIRONMENT_NAME,
            readiness.ALLOWLIST_KEY_ENVIRONMENT_NAME,
            readiness.RATE_LIMIT_KEY_ENVIRONMENT_NAME,
            readiness.INDEX_KEY_ENVIRONMENT_NAME,
        )
        for name in required:
            with self.subTest(missing=name):
                environment = _environment()
                environment.pop(name)
                expected = 404 if name == readiness.READINESS_TOKEN_ENVIRONMENT_NAME else 503
                self.assertEqual(_invoke(_Request(), environment=environment).status, expected)

        for name in required:
            with self.subTest(malformed=name):
                environment = _environment()
                environment[name] = "short"
                self.assertIn(
                    _invoke(_Request(), environment=environment).status,
                    {404} if name == readiness.READINESS_TOKEN_ENVIRONMENT_NAME else {503},
                )

    def test_all_encoded_secret_pairs_must_be_distinct(self):
        names = (
            readiness.READINESS_TOKEN_ENVIRONMENT_NAME,
            readiness.CSRF_KEY_ENVIRONMENT_NAME,
            readiness.CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME,
            readiness.ALLOWLIST_KEY_ENVIRONMENT_NAME,
            readiness.RATE_LIMIT_KEY_ENVIRONMENT_NAME,
            readiness.INDEX_KEY_ENVIRONMENT_NAME,
            readiness.INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME,
            readiness.MAILBOX_SECRET_ENVIRONMENT_NAME,
        )
        baseline = _environment(
            **{
                readiness.CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME: _b64(b"p" * 32),
                readiness.INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME: _b64(b"j" * 32),
                readiness.MAILBOX_SECRET_ENVIRONMENT_NAME: _b64(b"m" * 32),
            }
        )
        for left_index, left in enumerate(names):
            for right in names[left_index + 1 :]:
                with self.subTest(left=left, right=right):
                    environment = dict(baseline)
                    environment[right] = environment[left]
                    self.assertEqual(
                        _invoke(_Request(), environment=environment).status,
                        503,
                    )

    def test_session_secret_cannot_equal_encoded_text_or_decoded_material(self):
        encoded_text = _environment()[readiness.CSRF_KEY_ENVIRONMENT_NAME]
        self.assertEqual(
            _invoke(
                _Request(),
                environment=_environment(
                    **{readiness.SESSION_SECRET_ENVIRONMENT_NAME: encoded_text}
                ),
            ).status,
            503,
        )
        self.assertEqual(
            _invoke(
                _Request(),
                environment=_environment(
                    **{readiness.SESSION_SECRET_ENVIRONMENT_NAME: "c" * 32}
                ),
            ).status,
            503,
        )

    def test_malformed_optional_secrets_and_parser_errors_fail_closed(self):
        for name in (
            readiness.CSRF_PREVIOUS_KEY_ENVIRONMENT_NAME,
            readiness.INDEX_PREVIOUS_KEY_ENVIRONMENT_NAME,
            readiness.MAILBOX_SECRET_ENVIRONMENT_NAME,
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    _invoke(_Request(), environment=_environment(**{name: "bad!"})).status,
                    503,
                )
        with mock.patch.object(
            readiness.owner_request_security,
            "parse_owner_security_configuration",
            side_effect=RuntimeError("synthetic parser failure"),
        ):
            response = _invoke(_Request())
        self.assertEqual(response.status, 503)
        self.assertNotIn(b"synthetic", response.body)


class OwnerWriteReadinessRouteContainmentTests(unittest.TestCase):
    def test_route_is_default_closed_without_service_import(self):
        from . import owner_write_readiness as route

        request = _Request(guarded_reader=True)
        with mock.patch.dict(route.os.environ, {}, clear=True), mock.patch.object(
            route.importlib,
            "import_module",
            side_effect=AssertionError("inactive route imported service"),
        ):
            route.handler._respond(request)
        self.assertEqual(request.status, 404)
        self.assertFalse(any(name.lower() == "handler" for name in vars(readiness)))

    def test_route_requires_production_owner_read_and_exact_readiness_mode(self):
        from . import owner_write_readiness as route

        cases = (
            {
                "VERCEL_ENV": "preview",
                "CUEVION_COLLAB_V2_HTTP_MODE": "owner_read",
                route._READINESS_MODE_ENVIRONMENT_NAME: route._READINESS_MODE,
            },
            {
                "VERCEL_ENV": "production",
                "CUEVION_COLLAB_V2_HTTP_MODE": "owner_write",
                route._READINESS_MODE_ENVIRONMENT_NAME: route._READINESS_MODE,
            },
            {
                "VERCEL_ENV": "production",
                "CUEVION_COLLAB_V2_HTTP_MODE": "owner_read",
                route._READINESS_MODE_ENVIRONMENT_NAME: "off",
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                request = _Request(guarded_reader=True)
                with mock.patch.dict(
                    route.os.environ,
                    environment,
                    clear=True,
                ), mock.patch.object(
                    route.importlib,
                    "import_module",
                    side_effect=AssertionError("closed route imported service"),
                ):
                    route.handler._respond(request)
                self.assertEqual(request.status, 404)


class OwnerWriteReadinessDocumentationTests(unittest.TestCase):
    def test_retention_scope_and_runbook_safety_contract_are_explicit(self):
        root = Path(__file__).resolve().parents[2]
        activation = (root / "api/collaboration/V2_ACTIVATION_REQUIREMENTS.md").read_text()
        runbook = (root / "tools/COLLABORATION_OWNER_WRITE_RUNBOOK.md").read_text()
        for phrase in (
            "single-user private beta",
            "thread records, source pointers, and owner-append idempotency records",
            "external testers",
            "multi-user",
            "public beta",
        ):
            self.assertIn(phrase, activation)
        for phrase in (
            "maximum of 10 minutes",
            "gmail-carltricksmusic",
            "Do not append",
            "Keep the created test Collaboration record",
            "Do not manually delete Redis keys",
            "CUEVION_COLLAB_V2_HTTP_MODE=owner_read",
        ):
            self.assertIn(phrase, runbook)
