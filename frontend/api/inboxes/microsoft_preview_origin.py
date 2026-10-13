"""Fail-closed, exact-host Microsoft OAuth and outbound Preview authority.

This module is deliberately MICROSOFT-ONLY: it does not alter Gmail, IMAP or
global Auth0 canonical host enforcement. No wildcard Preview hosts, no dynamic
Host-derived redirect URIs, and no production-domain fallback in Preview.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from api.auth.http import (
    CANONICAL_APP_HOST,
    CANONICAL_APP_ORIGIN,
    HttpBoundaryError,
    get_unique_header,
    require_canonical_host,
    require_same_origin,
)

CALLBACK_PATH = "/api/inboxes/oauth-callback"
PREVIEW_ORIGIN_ENV = "CUEVION_MICROSOFT_PREVIEW_ORIGIN"
PUBLIC_APP_ORIGIN_ENV = "CUEVION_APP_URL"
MICROSOFT_REDIRECT_ENV = "MICROSOFT_OAUTH_REDIRECT_URI"
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Project-domain/branch association has been verified in Vercel. Keep the
# custom host pinned in SOURCE, not derived from an arbitrary env or request.
MICROSOFT_CUSTOM_PREVIEW_HOST = "mail-preview.cuevion.com"
MICROSOFT_CUSTOM_PREVIEW_BRANCH = "feature/microsoft-graph-runtime"
MICROSOFT_CUSTOM_PREVIEW_ORIGIN = "https://" + MICROSOFT_CUSTOM_PREVIEW_HOST


def _fail(code: str, status: int) -> None:
    raise HttpBoundaryError(code, status)


def _exact_vercel_preview_origin(value: object) -> tuple[str, str] | None:
    """Require byte-exact lowercase https://<deployment>.vercel.app origin."""
    if type(value) is not str or not value or len(value) > 300:
        return None
    try:
        uri = urlsplit(value)
        port = uri.port
    except ValueError:
        return None
    if (
        uri.scheme != "https"
        or port is not None
        or uri.path or uri.query or uri.fragment
        or uri.username is not None or uri.password is not None
        or uri.netloc != (uri.hostname or "")
        or not uri.hostname or not uri.hostname.endswith(".vercel.app")
    ):
        return None
    hostname = uri.hostname
    labels = hostname.split(".")
    if (
        not 3 <= len(labels) <= 12
        or labels[-2:] != ["vercel", "app"]
        or not all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        return None
    return value, hostname


def _exact_cuevion_preview_origin(value: object) -> tuple[str, str] | None:
    """The one expressly approved Preview-only Cuevion project subdomain."""
    if value != MICROSOFT_CUSTOM_PREVIEW_ORIGIN:
        return None
    return MICROSOFT_CUSTOM_PREVIEW_ORIGIN, MICROSOFT_CUSTOM_PREVIEW_HOST


def _preview_origin() -> tuple[str, str]:
    if os.getenv("VERCEL_ENV", "").strip().lower() != "preview":
        _fail("internal_error", 503)
    configured = os.getenv(PREVIEW_ORIGIN_ENV)
    desired = _exact_cuevion_preview_origin(configured)
    if desired is None:
        desired = _exact_vercel_preview_origin(configured)
    if desired is None:
        _fail("internal_error", 503)
    origin, hostname = desired
    if os.getenv(PUBLIC_APP_ORIGIN_ENV) != origin:
        _fail("internal_error", 503)
    if os.getenv(MICROSOFT_REDIRECT_ENV) != origin + CALLBACK_PATH:
        _fail("internal_error", 503)

    if hostname == MICROSOFT_CUSTOM_PREVIEW_HOST:
        # The project DNS maps this fixed host to precisely this Preview
        # branch. Defense-in-depth: deny the same host on any other Git ref.
        if os.getenv("VERCEL_GIT_COMMIT_REF") != MICROSOFT_CUSTOM_PREVIEW_BRANCH:
            _fail("internal_error", 503)
    else:
        # The older Vercel-only Preview mode stays equally restrictive:
        # deployment or branch hostname must be supplied by Vercel itself.
        vercel_urls = (
            os.getenv("VERCEL_URL", ""),
            os.getenv("VERCEL_BRANCH_URL", ""),
        )
        if hostname not in vercel_urls:
            _fail("internal_error", 503)
    return origin, hostname


def _exact_host(headers, hostname: str) -> None:
    host = get_unique_header(headers, "host", required=True)
    forwarded = get_unique_header(headers, "x-forwarded-host")
    forwarded_proto = get_unique_header(headers, "x-forwarded-proto")
    if (
        host != hostname
        or forwarded is not None and forwarded != hostname
        or forwarded_proto is not None and forwarded_proto != "https"
    ):
        _fail("forbidden_host", 403)


def require_microsoft_request_origin(headers, *, check_origin: bool = True) -> str:
    """Require an exact secure target host (and Origin for state changes).

    OAuth callbacks use check_origin=False: Microsoft redirects via GET with no
    Origin header. Browser POSTs (OAuth start, send, status) must have it.
    """
    environment = os.getenv("VERCEL_ENV", "").strip().lower()
    if environment == "production":
        require_canonical_host(headers)
        if check_origin:
            require_same_origin(headers)
        # Production never uses Preview env vars for authority.
        if os.getenv(MICROSOFT_REDIRECT_ENV) != CANONICAL_APP_ORIGIN + CALLBACK_PATH:
            _fail("internal_error", 503)
        return CANONICAL_APP_ORIGIN
    if environment != "preview":
        _fail("internal_error", 503)
    origin, hostname = _preview_origin()
    _exact_host(headers, hostname)
    if check_origin and get_unique_header(headers, "origin", required=True) != origin:
        _fail("forbidden_origin", 403)
    return origin


def require_microsoft_oauth_redirect(headers, *, callback: bool = False) -> str:
    """The OAuth start and callback MUST share an identical configured URI."""
    origin = require_microsoft_request_origin(headers, check_origin=not callback)
    return origin + CALLBACK_PATH
