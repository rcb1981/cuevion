"""Strict browser boundary for cookie-authenticated Team JSON mutations."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from api.auth.http import (
    CANONICAL_APP_ORIGIN,
    HttpBoundaryError,
    get_unique_header,
)


_CONTENT_TYPE = "application/json"
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _reject(code: str, status: int) -> None:
    raise HttpBoundaryError(code, status) from None


def _normalize_origin(value: object, *, allow_http: bool) -> str | None:
    if type(value) is not str or not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname.lower() if type(parsed.hostname) is str else ""
    scheme = parsed.scheme.lower()
    if (
        not hostname
        or not parsed.netloc
        or scheme not in ({"http", "https"} if allow_http else {"https"})
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in value)
    ):
        return None
    normalized_host = f"[{hostname}]" if ":" in hostname else hostname
    normalized_netloc = f"{normalized_host}:{port}" if port is not None else normalized_host
    if (
        parsed.netloc.lower() != normalized_netloc.lower()
        or (scheme == "http" and hostname not in _LOCAL_HTTP_HOSTS)
    ):
        return None
    return f"{scheme}://{normalized_netloc}"


def _expected_origin(
    headers: tuple[tuple[str, str], ...],
    environment: Mapping[str, str],
) -> str:
    deployment = str(environment.get("VERCEL_ENV") or "").strip().lower()
    if deployment == "production":
        return CANONICAL_APP_ORIGIN

    configured = environment.get("CUEVION_APP_URL")
    if type(configured) is str and configured:
        normalized = _normalize_origin(
            configured.rstrip("/"),
            allow_http=deployment in {"", "development"},
        )
        if normalized is None:
            _reject("invalid_request", 400)
        return normalized

    origin = get_unique_header(headers, "origin", required=True)
    normalized = _normalize_origin(
        origin,
        allow_http=deployment in {"", "development"},
    )
    if normalized is None:
        _reject("forbidden_origin", 403)
    return normalized


def require_safe_json_mutation(
    headers: tuple[tuple[str, str], ...],
    environment: Mapping[str, str] | None = None,
) -> str:
    """Require non-simple JSON and return the exact trusted request origin."""

    source = os.environ if environment is None else environment
    content_type = get_unique_header(headers, "content-type", required=True)
    if content_type != _CONTENT_TYPE:
        _reject("invalid_request", 415)

    origin = get_unique_header(headers, "origin", required=True)
    expected_origin = _expected_origin(headers, source)
    if origin != expected_origin:
        _reject("forbidden_origin", 403)

    parsed = urlsplit(expected_origin)
    expected_host = parsed.netloc
    host = get_unique_header(headers, "host", required=True)
    forwarded_host = get_unique_header(headers, "x-forwarded-host")
    forwarded_proto = get_unique_header(headers, "x-forwarded-proto")
    if host != expected_host or (
        forwarded_host is not None and forwarded_host != expected_host
    ):
        _reject("forbidden_host", 403)
    if forwarded_proto is not None and forwarded_proto != parsed.scheme:
        _reject("forbidden_origin", 403)
    return expected_origin


__all__ = ("require_safe_json_mutation",)
