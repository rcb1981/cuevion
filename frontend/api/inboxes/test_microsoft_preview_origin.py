"""Exact Microsoft Preview origin allowlist: no wildcard or cross-env fallback."""
from __future__ import annotations

import os
import unittest
from email.message import Message
from unittest.mock import patch

from api.auth.http import HttpBoundaryError
from api.inboxes import microsoft_preview_origin as authority

ORIGIN = "https://cuevion-microsoft-test-rutgers-projects.vercel.app"
HOST = "cuevion-microsoft-test-rutgers-projects.vercel.app"
REDIRECT = ORIGIN + authority.CALLBACK_PATH

PROD = {
    "VERCEL_ENV": "production",
    "MICROSOFT_OAUTH_REDIRECT_URI": "https://app.cuevion.com" + authority.CALLBACK_PATH,
}
PREVIEW = {
    "VERCEL_ENV": "preview",
    "CUEVION_MICROSOFT_PREVIEW_ORIGIN": ORIGIN,
    "CUEVION_APP_URL": ORIGIN,
    "MICROSOFT_OAUTH_REDIRECT_URI": REDIRECT,
    "VERCEL_URL": "some-other-deployment.vercel.app",
    "VERCEL_BRANCH_URL": HOST,
}


def headers(*, host=HOST, origin=ORIGIN, forwarded=None, forwarded_proto="https") -> tuple:
    result = [("Host", host)]
    if origin is not None:
        result.append(("Origin", origin))
    if forwarded is not None:
        result.append(("X-Forwarded-Host", forwarded))
    if forwarded_proto is not None:
        result.append(("X-Forwarded-Proto", forwarded_proto))
    return tuple(result)


def rejects(code: str, callback, *args, **kwargs):
    with unittest.TestCase().assertRaises(HttpBoundaryError) as caught:
        callback(*args, **kwargs)
    assert caught.exception.code == code, caught.exception.code


CUSTOM_ORIGIN = "https://mail-preview.cuevion.com"
CUSTOM_HOST = "mail-preview.cuevion.com"
CUSTOM_PREVIEW = {
    **PREVIEW,
    "CUEVION_MICROSOFT_PREVIEW_ORIGIN": CUSTOM_ORIGIN,
    "CUEVION_APP_URL": CUSTOM_ORIGIN,
    "MICROSOFT_OAUTH_REDIRECT_URI": CUSTOM_ORIGIN + authority.CALLBACK_PATH,
    "VERCEL_GIT_COMMIT_REF": "feature/microsoft-graph-runtime",
}


class ExactPreviewOriginTests(unittest.TestCase):
    def test_fixed_custom_preview_host_and_oauth_callback_are_allowed(self):
        with patch.dict(os.environ, CUSTOM_PREVIEW, clear=True):
            self.assertEqual(
                authority.require_microsoft_request_origin(
                    headers(host=CUSTOM_HOST, origin=CUSTOM_ORIGIN),
                ),
                CUSTOM_ORIGIN,
            )
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(
                    headers(host=CUSTOM_HOST, origin=CUSTOM_ORIGIN),
                ),
                CUSTOM_ORIGIN + authority.CALLBACK_PATH,
            )
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(
                    headers(host=CUSTOM_HOST, origin=None), callback=True,
                ),
                CUSTOM_ORIGIN + authority.CALLBACK_PATH,
            )

    def test_custom_preview_rejects_wrong_git_ref_or_missing_ref(self):
        for git_ref in ("", "main", "feature/other", "feature/microsoft-graph-runtime-old"):
            with self.subTest(ref=git_ref), patch.dict(
                os.environ, {**CUSTOM_PREVIEW, "VERCEL_GIT_COMMIT_REF": git_ref},
                clear=True,
            ):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_oauth_redirect(
                        headers(host=CUSTOM_HOST, origin=CUSTOM_ORIGIN),
                    )
                self.assertEqual(caught.exception.status, 503)

    def test_custom_preview_never_accepts_unapproved_custom_domain(self):
        for origin in (
            "https://preview.cuevion.com",
            "https://mail-preview.cuevion.com.evil.example",
            "http://mail-preview.cuevion.com",
            "https://MAIL-PREVIEW.cuevion.com",
            "https://mail-preview.cuevion.com/",
            "https://mail-preview.cuevion.com:443",
            "https://mail-preview.cuevion.com?x=1",
            "https://app.cuevion.com",
        ):
            with self.subTest(origin=origin):
                self.assertIsNone(authority._exact_cuevion_preview_origin(origin))
                config = {
                    **CUSTOM_PREVIEW,
                    "CUEVION_MICROSOFT_PREVIEW_ORIGIN": origin,
                    "CUEVION_APP_URL": origin,
                    "MICROSOFT_OAUTH_REDIRECT_URI": origin + authority.CALLBACK_PATH,
                }
                with patch.dict(os.environ, config, clear=True):
                    with self.assertRaises(HttpBoundaryError):
                        authority.require_microsoft_request_origin(
                            headers(host=origin.removeprefix("https://"),
                                    origin=origin),
                        )

    def test_custom_preview_requires_three_matching_config_values(self):
        for changed in (
            {"CUEVION_APP_URL": "https://app.cuevion.com"},
            {"MICROSOFT_OAUTH_REDIRECT_URI": CUSTOM_ORIGIN},
            {"MICROSOFT_OAUTH_REDIRECT_URI": CUSTOM_ORIGIN + "/wrong"},
            {"CUEVION_MICROSOFT_PREVIEW_ORIGIN": ""},
        ):
            with self.subTest(changed=changed), patch.dict(
                os.environ, {**CUSTOM_PREVIEW, **changed}, clear=True,
            ):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_oauth_redirect(
                        headers(host=CUSTOM_HOST, origin=CUSTOM_ORIGIN),
                    )
                self.assertEqual(caught.exception.status, 503)

    def test_custom_preview_rejects_host_origin_or_forwarding_spoofing(self):
        for settings in (
            {"host": "app.cuevion.com", "origin": CUSTOM_ORIGIN},
            {"host": CUSTOM_HOST, "origin": "https://app.cuevion.com"},
            {"host": CUSTOM_HOST, "origin": CUSTOM_ORIGIN,
             "forwarded": "other.cuevion.com"},
            {"host": CUSTOM_HOST, "origin": CUSTOM_ORIGIN,
             "forwarded_proto": "http"},
        ):
            with self.subTest(settings=settings), patch.dict(
                os.environ, CUSTOM_PREVIEW, clear=True,
            ):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_request_origin(headers(**settings))
                self.assertEqual(caught.exception.code, (
                    "forbidden_host" if settings.get("host") != CUSTOM_HOST
                    or settings.get("forwarded") or
                    settings.get("forwarded_proto") == "http"
                    else "forbidden_origin"
                ))

    def test_custom_preview_does_not_enable_custom_host_in_production(self):
        production = {
            **CUSTOM_PREVIEW,
            "VERCEL_ENV": "production",
            "MICROSOFT_OAUTH_REDIRECT_URI":
                "https://app.cuevion.com" + authority.CALLBACK_PATH,
        }
        with patch.dict(os.environ, production, clear=True):
            with self.assertRaises(HttpBoundaryError):
                authority.require_microsoft_request_origin(
                    headers(host=CUSTOM_HOST, origin=CUSTOM_ORIGIN),
                )
            self.assertEqual(
                authority.require_microsoft_request_origin(
                    headers(host="app.cuevion.com",
                            origin="https://app.cuevion.com"),
                ),
                "https://app.cuevion.com",
            )

    def test_production_is_exactly_canonical_and_ignores_preview_config(self):
        config = {
            **PROD,
            "CUEVION_MICROSOFT_PREVIEW_ORIGIN": ORIGIN,
            "CUEVION_APP_URL": ORIGIN,
        }
        with patch.dict(os.environ, config, clear=True):
            current = headers(host="app.cuevion.com", origin="https://app.cuevion.com")
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(current),
                "https://app.cuevion.com" + authority.CALLBACK_PATH,
            )
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(
                    headers(host="app.cuevion.com", origin=None), callback=True,
                ),
                "https://app.cuevion.com" + authority.CALLBACK_PATH,
            )
            rejects("forbidden_host", authority.require_microsoft_request_origin, headers())

    def test_production_with_wrong_redirect_fails_closed(self):
        with patch.dict(os.environ, {**PROD, "MICROSOFT_OAUTH_REDIRECT_URI": REDIRECT}, clear=True):
            with self.assertRaises(HttpBoundaryError) as caught:
                authority.require_microsoft_oauth_redirect(
                    headers(host="app.cuevion.com", origin="https://app.cuevion.com"),
                )
            self.assertEqual(caught.exception.status, 503)

    def test_exact_preview_branch_alias_and_redirect(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(headers()),
                REDIRECT,
            )
            self.assertEqual(
                authority.require_microsoft_oauth_redirect(
                    headers(origin=None), callback=True,
                ),
                REDIRECT,
            )

    def test_preview_unique_deployment_url_is_also_supported_if_pinned(self):
        settings = {
            **PREVIEW,
            "VERCEL_URL": HOST,
            "VERCEL_BRANCH_URL": "",
        }
        with patch.dict(os.environ, settings, clear=True):
            self.assertEqual(authority.require_microsoft_request_origin(headers()), ORIGIN)

    def test_invalid_preview_origins_are_rejected(self):
        invalid = (
            "http://" + HOST,
            "https://*.vercel.app",
            "https://vercel.app",
            "https://fakevercel.app",
            "https://evil.example",
            ORIGIN + "/",
            ORIGIN + "/oauth-callback",
            ORIGIN + "?token=1",
            ORIGIN + "#fragment",
            "https://user@" + HOST,
            "https://"+HOST+":443",
            "https://"+HOST.upper(),
            ORIGIN.replace("test-", "te st-"),
            "https://-bad.vercel.app",
            "https://bad-.vercel.app",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(authority._exact_vercel_preview_origin(value))

    def test_unconfigured_preview_variable_is_not_an_allowlist(self):
        for name in (
            "CUEVION_MICROSOFT_PREVIEW_ORIGIN",
            "CUEVION_APP_URL",
            "MICROSOFT_OAUTH_REDIRECT_URI",
            "VERCEL_URL",
            "VERCEL_BRANCH_URL",
        ):
            config = dict(PREVIEW)
            if name in ("VERCEL_URL", "VERCEL_BRANCH_URL"):
                config["VERCEL_URL"] = ""
                config["VERCEL_BRANCH_URL"] = ""
            else:
                config.pop(name)
            with self.subTest(name=name), patch.dict(os.environ, config, clear=True):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_oauth_redirect(headers())
                self.assertEqual(caught.exception.status, 503)

    def test_untrusted_preview_origin_cannot_override_fixed_host(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            for pair in (
                ("https://other.vercel.app", "https://other.vercel.app"),
                (ORIGIN, "other.vercel.app"),
                ("https://evil.example", HOST),
            ):
                browser_origin, requested_host = pair
                with self.subTest(pair=pair), self.assertRaises(HttpBoundaryError):
                    authority.require_microsoft_request_origin(
                        headers(host=requested_host, origin=browser_origin),
                    )

    def test_duplicate_origin_and_host_headers_are_denied(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            for name, value, base in (
                ("Host", "other.vercel.app", headers()),
                ("Origin", "https://other.vercel.app", headers()),
                ("X-Forwarded-Host", HOST, headers(forwarded=HOST)),
            ):
                with self.subTest(name=name):
                    with self.assertRaises(HttpBoundaryError) as caught:
                        authority.require_microsoft_request_origin(
                            base + ((name, value),),
                        )
                    self.assertEqual(caught.exception.code, "ambiguous_headers")

    def test_forwarded_host_and_protocol_are_byte_exact(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            self.assertEqual(authority.require_microsoft_request_origin(
                headers(forwarded=HOST),
            ), ORIGIN)
            for bad in (
                headers(forwarded="other.vercel.app"),
                headers(forwarded_proto="http"),
                headers(forwarded_proto="HTTPS"),
            ):
                with self.subTest(bad=bad), self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_request_origin(bad)
                self.assertEqual(caught.exception.code, "forbidden_host")

    def test_callback_can_omit_origin_but_must_match_host(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            self.assertEqual(authority.require_microsoft_oauth_redirect(
                headers(origin=None), callback=True,
            ), REDIRECT)
            with self.assertRaises(HttpBoundaryError):
                authority.require_microsoft_oauth_redirect(
                    headers(host="wrong.vercel.app", origin=None), callback=True,
                )

    def test_oauth_start_cannot_omit_origin(self):
        with patch.dict(os.environ, PREVIEW, clear=True):
            with self.assertRaises(HttpBoundaryError):
                authority.require_microsoft_oauth_redirect(headers(origin=None))

    def test_non_preview_non_production_never_authorized(self):
        for environment in ("", "development", "staging"):
            with self.subTest(environment=environment), patch.dict(os.environ, {
                **PREVIEW, "VERCEL_ENV": environment,
            }, clear=True):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_request_origin(headers())
                self.assertEqual(caught.exception.status, 503)

    def test_stored_origin_and_redirect_must_equal_exact_pin(self):
        for updates in (
            {"CUEVION_APP_URL": "https://other.vercel.app"},
            {"MICROSOFT_OAUTH_REDIRECT_URI": REDIRECT + "/"},
            {"MICROSOFT_OAUTH_REDIRECT_URI": "https://app.cuevion.com/api/inboxes/oauth-callback"},
            {"CUEVION_MICROSOFT_PREVIEW_ORIGIN": "https://other.vercel.app"},
        ):
            with self.subTest(updates=updates), patch.dict(os.environ, {**PREVIEW, **updates}, clear=True):
                with self.assertRaises(HttpBoundaryError) as caught:
                    authority.require_microsoft_oauth_redirect(headers())
                self.assertIn(caught.exception.status, (403, 503))


if __name__ == "__main__":
    unittest.main()
