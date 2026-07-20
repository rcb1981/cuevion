"""Strict HTTP helpers shared by Cuevion's Auth0 route adapters.

The helpers in this module are deliberately transport-only.  They do not read
environment variables, perform network I/O, authenticate a user, or choose a
redirect target.  Request headers remain duplicate-preserving until a caller
asks for one exact value.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


CANONICAL_APP_HOST = "app.cuevion.com"
CANONICAL_APP_ORIGIN = "https://app.cuevion.com"

SECURITY_RESPONSE_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)

_MAX_HEADER_PAIRS = 64
_MAX_HEADER_NAME_CHARACTERS = 128
_MAX_HEADER_VALUE_BYTES = 8_192
_MAX_TOTAL_HEADER_BYTES = 32_768
_MAX_COOKIE_HEADER_BYTES = 8_192
_MAX_SET_COOKIE_BYTES = 4_096
_MAX_LOCATION_BYTES = 4_096
_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_EXTRA_RESPONSE_HEADER_NAMES = frozenset({"allow", "location", "retry-after"})
_UNIQUE_SECURITY_HEADERS = frozenset(
    {
        "content-length",
        "content-type",
        "cookie",
        "host",
        "origin",
        "transfer-encoding",
        "x-forwarded-host",
    }
)
_SAFE_ERROR_CODES = frozenset(
    {
        "ambiguous_headers",
        "forbidden_host",
        "forbidden_origin",
        "internal_error",
        "invalid_headers",
        "invalid_request",
        "method_not_allowed",
    }
)


class HttpBoundaryError(Exception):
    """A fixed, request-safe rejection from the auth HTTP boundary."""

    __slots__ = ("code", "status")

    def __init__(self, code: str, status: int) -> None:
        safe_code = code if code in _SAFE_ERROR_CODES else "internal_error"
        safe_status = status if type(status) is int and 400 <= status <= 599 else 500
        self.code = safe_code
        self.status = safe_status
        Exception.__init__(self, safe_code)

    def __repr__(self) -> str:
        return f"HttpBoundaryError({self.code!r}, {self.status})"


def _reject(code: str, status: int) -> None:
    error = HttpBoundaryError(code, status)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _contains_prohibited_character(value: str) -> bool:
    return any(
        ord(character) <= 31
        or ord(character) == 127
        or unicodedata.category(character) in {"Cf", "Cs"}
        for character in value
    )


def validate_header_pairs(raw_headers: object) -> tuple[tuple[str, str], ...]:
    """Return an immutable, bounded, duplicate-preserving header snapshot."""

    if type(raw_headers) not in (list, tuple) or len(raw_headers) > _MAX_HEADER_PAIRS:
        _reject("invalid_headers", 400)

    result: list[tuple[str, str]] = []
    total_bytes = 0
    for pair in raw_headers:
        if type(pair) not in (list, tuple) or len(pair) != 2:
            _reject("invalid_headers", 400)
        name, value = pair
        if (
            type(name) is not str
            or not (1 <= len(name) <= _MAX_HEADER_NAME_CHARACTERS)
            or not name.isascii()
            or _HTTP_TOKEN_RE.fullmatch(name) is None
            or type(value) is not str
            or _contains_prohibited_character(value)
        ):
            _reject("invalid_headers", 400)
        try:
            value_bytes = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _reject("invalid_headers", 400)
        if len(value_bytes) > _MAX_HEADER_VALUE_BYTES:
            _reject("invalid_headers", 400)
        total_bytes += len(name) + len(value_bytes)
        if total_bytes > _MAX_TOTAL_HEADER_BYTES:
            _reject("invalid_headers", 400)
        result.append((name, value))
    return tuple(result)


def snapshot_request_headers(request: object) -> tuple[tuple[str, str], ...]:
    """Snapshot ``BaseHTTPRequestHandler.headers.raw_items()`` safely."""

    try:
        raw_items = request.headers.raw_items  # type: ignore[attr-defined]
        if not callable(raw_items):
            raise TypeError
        raw_result = raw_items()
        if type(raw_result) not in (list, tuple) and type(raw_result) is not type(
            iter([])
        ):
            raise TypeError
        snapshot = list(raw_result)
    except Exception:
        _reject("invalid_headers", 400)
    return validate_header_pairs(snapshot)


def get_unique_header(
    headers: object,
    name: str,
    *,
    required: bool = False,
) -> str | None:
    """Return exactly one case-insensitive header value or reject ambiguity."""

    if (
        type(name) is not str
        or name != name.lower()
        or _HTTP_TOKEN_RE.fullmatch(name) is None
        or type(required) is not bool
    ):
        raise ValueError("invalid trusted header configuration")
    validated = validate_header_pairs(headers)
    values = tuple(
        value for header_name, value in validated if header_name.lower() == name
    )
    if len(values) > 1:
        _reject("ambiguous_headers", 400)
    if not values:
        if required:
            _reject("invalid_request", 400)
        return None
    value = values[0]
    if name in _UNIQUE_SECURITY_HEADERS and name != "cookie" and "," in value:
        _reject("ambiguous_headers", 400)
    return value


def require_method(method: object, expected: str) -> str:
    """Require one exact uppercase HTTP method."""

    if (
        type(expected) is not str
        or expected != expected.upper()
        or not expected.isascii()
        or _HTTP_TOKEN_RE.fullmatch(expected) is None
    ):
        raise ValueError("invalid trusted method configuration")
    if (
        type(method) is not str
        or not method.isascii()
        or _HTTP_TOKEN_RE.fullmatch(method) is None
        or method != expected
    ):
        _reject("method_not_allowed", 405)
    return method


def require_canonical_host(headers: object) -> str:
    """Require the byte-exact production Host and any forwarded Host copy."""

    host = get_unique_header(headers, "host", required=True)
    forwarded_host = get_unique_header(headers, "x-forwarded-host")
    if host != CANONICAL_APP_HOST or (
        forwarded_host is not None and forwarded_host != CANONICAL_APP_HOST
    ):
        _reject("forbidden_host", 403)
    return CANONICAL_APP_HOST


def require_same_origin(headers: object) -> str:
    """Require the byte-exact production Origin for a state-changing request."""

    origin = get_unique_header(headers, "origin", required=True)
    if origin != CANONICAL_APP_ORIGIN:
        _reject("forbidden_origin", 403)
    return CANONICAL_APP_ORIGIN


def _is_cookie_octet(character: str) -> bool:
    codepoint = ord(character)
    return (
        codepoint == 0x21
        or 0x23 <= codepoint <= 0x2B
        or 0x2D <= codepoint <= 0x3A
        or 0x3C <= codepoint <= 0x5B
        or 0x5D <= codepoint <= 0x7E
    )


def read_cookie(headers: object, cookie_name: str) -> str | None:
    """Read one exact cookie while rejecting ambiguous Cookie representations."""

    if (
        type(cookie_name) is not str
        or not cookie_name
        or not cookie_name.isascii()
        or _HTTP_TOKEN_RE.fullmatch(cookie_name) is None
    ):
        raise ValueError("invalid trusted cookie configuration")
    raw_cookie = get_unique_header(headers, "cookie")
    if raw_cookie is None:
        return None
    if (
        not raw_cookie.isascii()
        or len(raw_cookie) > _MAX_COOKIE_HEADER_BYTES
        or "," in raw_cookie
        or "\t" in raw_cookie
        or '"' in raw_cookie
        or "\\" in raw_cookie
    ):
        _reject("invalid_request", 400)

    segments = raw_cookie.split(";")
    if not segments or any(not segment for segment in segments):
        _reject("invalid_request", 400)
    names: set[str] = set()
    selected: str | None = None
    selected_count = 0
    for index, raw_segment in enumerate(segments):
        segment = raw_segment
        if index and segment.startswith(" "):
            segment = segment[1:]
            if segment.startswith(" "):
                _reject("invalid_request", 400)
        if not segment or "=" not in segment:
            _reject("invalid_request", 400)
        name, value = segment.split("=", 1)
        if (
            not name
            or _HTTP_TOKEN_RE.fullmatch(name) is None
            or any(not _is_cookie_octet(character) for character in value)
            or name in names
        ):
            _reject("invalid_request", 400)
        names.add(name)
        if name == cookie_name:
            selected_count += 1
            selected = value
    if selected_count > 1:
        _reject("ambiguous_headers", 400)
    return selected if selected else None


@dataclass(frozen=True, slots=True, repr=False)
class PublicResponse:
    """A complete response that a route adapter can emit without mutation."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __repr__(self) -> str:
        return f"PublicResponse(status={self.status}, <redacted>)"


def _validated_status(status: object) -> int:
    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError("invalid trusted response status")
    return status


def _validated_set_cookies(values: object) -> tuple[str, ...]:
    if type(values) not in (list, tuple):
        raise ValueError("invalid trusted cookie response")
    result: list[str] = []
    for value in values:
        if (
            type(value) is not str
            or not value.isascii()
            or not value
            or len(value) > _MAX_SET_COOKIE_BYTES
            or _contains_prohibited_character(value)
        ):
            raise ValueError("invalid trusted cookie response")
        result.append(value)
    return tuple(result)


def _validated_extra_headers(values: object) -> tuple[tuple[str, str], ...]:
    if type(values) not in (list, tuple):
        raise ValueError("invalid trusted response headers")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pair in values:
        if type(pair) not in (list, tuple) or len(pair) != 2:
            raise ValueError("invalid trusted response headers")
        name, value = pair
        lowered = name.lower() if type(name) is str else ""
        if (
            type(name) is not str
            or type(value) is not str
            or lowered not in _EXTRA_RESPONSE_HEADER_NAMES
            or lowered in seen
            or not name.isascii()
            or not value.isascii()
            or not value
            or _contains_prohibited_character(value)
        ):
            raise ValueError("invalid trusted response headers")
        seen.add(lowered)
        result.append((name, value))
    return tuple(result)


def json_response(
    status: int,
    payload: dict[str, Any],
    *,
    set_cookies: object = (),
    extra_headers: object = (),
) -> PublicResponse:
    """Encode one compact JSON response with the fixed auth security headers."""

    validated_status = _validated_status(status)
    if type(payload) is not dict:
        raise ValueError("invalid trusted JSON response")
    try:
        body = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("invalid trusted JSON response") from None
    cookies = _validated_set_cookies(set_cookies)
    extras = _validated_extra_headers(extra_headers)
    headers = (
        ("Content-Type", "application/json; charset=utf-8"),
        *SECURITY_RESPONSE_HEADERS,
        *(('Set-Cookie', cookie) for cookie in cookies),
        *extras,
        ("Content-Length", str(len(body))),
    )
    return PublicResponse(validated_status, tuple(headers), body)


def redirect_response(
    location: str,
    *,
    status: int = 303,
    set_cookies: object = (),
) -> PublicResponse:
    """Build a bodyless redirect to a caller-owned, already trusted location."""

    if status not in (302, 303) or type(status) is not int:
        raise ValueError("invalid trusted redirect status")
    if (
        type(location) is not str
        or not location.isascii()
        or not location
        or len(location) > _MAX_LOCATION_BYTES
        or _contains_prohibited_character(location)
    ):
        raise ValueError("invalid trusted redirect location")
    cookies = _validated_set_cookies(set_cookies)
    headers = (
        *SECURITY_RESPONSE_HEADERS,
        *(('Set-Cookie', cookie) for cookie in cookies),
        ("Location", location),
        ("Content-Length", "0"),
    )
    return PublicResponse(status, tuple(headers), b"")


def send_public_response(handler: object, response: PublicResponse) -> None:
    """Emit one validated response through a ``BaseHTTPRequestHandler`` object."""

    if type(response) is not PublicResponse:
        raise ValueError("invalid trusted public response")
    _validated_status(response.status)
    if type(response.headers) is not tuple or type(response.body) is not bytes:
        raise ValueError("invalid trusted public response")
    for pair in response.headers:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            or not pair[0].isascii()
            or not pair[1].isascii()
            or _HTTP_TOKEN_RE.fullmatch(pair[0]) is None
            or _contains_prohibited_character(pair[1])
        ):
            raise ValueError("invalid trusted public response")
    try:
        command = getattr(handler, "command", None)
        send_response_only = handler.send_response_only  # type: ignore[attr-defined]
        send_header = handler.send_header  # type: ignore[attr-defined]
        end_headers = handler.end_headers  # type: ignore[attr-defined]
        write = handler.wfile.write  # type: ignore[attr-defined]
    except Exception:
        raise ValueError("invalid response handler") from None
    if not all(
        callable(value)
        for value in (send_response_only, send_header, end_headers, write)
    ):
        raise ValueError("invalid response handler")
    send_response_only(response.status)
    for name, value in response.headers:
        send_header(name, value)
    end_headers()
    if response.body and command != "HEAD":
        write(response.body)


__all__ = (
    "CANONICAL_APP_HOST",
    "CANONICAL_APP_ORIGIN",
    "SECURITY_RESPONSE_HEADERS",
    "HttpBoundaryError",
    "PublicResponse",
    "validate_header_pairs",
    "snapshot_request_headers",
    "get_unique_header",
    "require_method",
    "require_canonical_host",
    "require_same_origin",
    "read_cookie",
    "json_response",
    "redirect_response",
    "send_public_response",
)
