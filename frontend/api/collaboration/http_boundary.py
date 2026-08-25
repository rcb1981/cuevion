from __future__ import annotations

if __name__ != "api.collaboration.http_boundary":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.http_boundary"
    )

import json
import re
import unicodedata
from typing import Any, Pattern


RawHeaderPair = tuple[str, str]
ValidatedHeaderPairs = tuple[RawHeaderPair, ...]

_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTENT_LENGTH_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_SECURITY_HEADER_NAMES = frozenset(
    {
        "origin",
        "content-type",
        "cookie",
        "x-cuevion-csrf",
        "x-cuevion-idempotency-key",
        "content-length",
        "transfer-encoding",
    }
)
_JSON_CONTENT_TYPES = frozenset(
    {"application/json", "application/json; charset=utf-8"}
)


class BoundaryError(Exception):
    """A request rejection containing only adapter-safe structured details."""

    __slots__ = ("code", "status")

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class _DuplicateObjectKey(Exception):
    pass


class _ForbiddenJsonNumber(Exception):
    pass


class _ForbiddenJsonConstant(Exception):
    pass


def _reject(code: str, status: int) -> None:
    raise BoundaryError(code, status)


def _has_prohibited_unicode_category(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    )


def validate_raw_headers(headers: object) -> ValidatedHeaderPairs:
    """Return an immutable snapshot without normalizing names, values, or order.

    Only exact built-in list and tuple containers are accepted. This keeps
    duplicate request headers observable without executing caller-defined
    container behavior.
    """

    if type(headers) not in (list, tuple):
        _reject("invalid_headers", 400)

    validated: list[RawHeaderPair] = []
    for pair in headers:
        if type(pair) not in (list, tuple) or len(pair) != 2:
            _reject("invalid_headers", 400)
        name, value = pair
        if (
            type(name) is not str
            or not name.isascii()
            or _HTTP_TOKEN_RE.fullmatch(name) is None
            or _has_prohibited_unicode_category(name)
            or type(value) is not str
            or _has_prohibited_unicode_category(value)
        ):
            _reject("invalid_headers", 400)
        validated.append((name, value))
    return tuple(validated)


def validate_security_headers(headers: object) -> ValidatedHeaderPairs:
    """Reject ambiguous security headers while preserving the raw sequence."""

    validated = validate_raw_headers(headers)
    seen: set[str] = set()
    has_transfer_encoding = False
    for name, value in validated:
        normalized_name = name.lower()
        if normalized_name not in _SECURITY_HEADER_NAMES:
            continue
        if normalized_name in seen or "," in value:
            _reject("ambiguous_headers", 400)
        seen.add(normalized_name)
        if normalized_name == "transfer-encoding":
            has_transfer_encoding = True
    if has_transfer_encoding:
        _reject("invalid_framing", 400)
    return validated


def get_security_header(
    headers: object,
    name: str,
    *,
    required: bool = False,
) -> str | None:
    """Return the sole security-header value after strict cardinality checks."""

    if (
        type(name) is not str
        or name != name.lower()
        or name not in _SECURITY_HEADER_NAMES
        or type(required) is not bool
    ):
        raise ValueError("invalid security header configuration")
    validated = validate_security_headers(headers)
    value = next(
        (value for header_name, value in validated if header_name.lower() == name),
        None,
    )
    if value is None and required:
        _reject("missing_header", 400)
    return value


def require_method(method: object, expected: str) -> str:
    """Require one exact uppercase ASCII HTTP token."""

    if (
        type(expected) is not str
        or not expected.isascii()
        or expected != expected.upper()
        or _HTTP_TOKEN_RE.fullmatch(expected) is None
    ):
        raise ValueError("invalid expected method configuration")
    if (
        type(method) is not str
        or not method.isascii()
        or _HTTP_TOKEN_RE.fullmatch(method) is None
        or method != expected
    ):
        _reject("method_not_allowed", 405)
    return method


def require_json_content_type(headers: object) -> str:
    """Require one of the two exact Collaboration JSON media types."""

    content_type = get_security_header(headers, "content-type")
    if content_type not in _JSON_CONTENT_TYPES:
        _reject("unsupported_content_type", 415)
    return content_type


def require_bounded_body(
    headers: object,
    body: object,
    *,
    maximum_bytes: int,
    require_content_length: bool = True,
) -> bytes:
    """Validate framing for already-supplied bytes without reading a socket."""

    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 0
        or type(require_content_length) is not bool
    ):
        raise ValueError("invalid body limit configuration")
    if type(body) is not bytes:
        _reject("invalid_framing", 400)

    validated = validate_security_headers(headers)
    if len(body) > maximum_bytes:
        _reject("payload_too_large", 413)
    content_length = next(
        (
            value
            for header_name, value in validated
            if header_name.lower() == "content-length"
        ),
        None,
    )
    if content_length is None:
        if require_content_length:
            _reject("invalid_framing", 400)
        return body

    if (
        not content_length.isascii()
        or _CONTENT_LENGTH_RE.fullmatch(content_length) is None
    ):
        _reject("invalid_framing", 400)

    maximum_text = str(maximum_bytes)
    if (
        len(content_length) > len(maximum_text)
        or (
            len(content_length) == len(maximum_text)
            and content_length > maximum_text
        )
    ):
        _reject("payload_too_large", 413)

    if int(content_length) != len(body):
        _reject("invalid_framing", 400)
    return body


def decode_strict_utf8(body: object) -> str:
    """Decode UTF-8 without replacement and reject a leading UTF-8 BOM."""

    if type(body) is not bytes:
        _reject("invalid_framing", 400)
    if body.startswith(b"\xef\xbb\xbf"):
        _reject("invalid_utf8", 400)
    decoded: str | None = None
    try:
        decoded = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    if decoded is None:
        _reject("invalid_utf8", 400)
    return decoded


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _DuplicateObjectKey
        result[key] = value
    return result


def _reject_json_number(_value: str) -> Any:
    raise _ForbiddenJsonNumber


def _reject_json_constant(_value: str) -> Any:
    raise _ForbiddenJsonConstant


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _has_invalid_json_string(value: Any) -> bool:
    if type(value) is str:
        return _contains_surrogate(value)
    if type(value) is list:
        return any(_has_invalid_json_string(item) for item in value)
    if type(value) is dict:
        return any(
            type(key) is not str
            or _contains_surrogate(key)
            or _has_invalid_json_string(item)
            for key, item in value.items()
        )
    return False


def _field_names(values: object, label: str) -> frozenset[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"invalid {label} configuration")
    fields: frozenset[Any] = frozenset()
    conversion_failed = False
    try:
        fields = frozenset(values)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        conversion_failed = True
    if conversion_failed:
        raise ValueError(f"invalid {label} configuration")
    if any(type(field) is not str for field in fields):
        raise ValueError(f"invalid {label} configuration")
    return fields


def parse_json_object(
    text: object,
    *,
    allowed_fields: object,
    required_fields: object = (),
    reject_numbers: bool = True,
) -> dict[str, Any]:
    """Parse one strict object and enforce an exact top-level field schema."""

    allowed = _field_names(allowed_fields, "allowed fields")
    required = _field_names(required_fields, "required fields")
    if not required.issubset(allowed) or type(reject_numbers) is not bool:
        raise ValueError("invalid JSON schema configuration")
    if type(text) is not str:
        _reject("invalid_json", 400)

    parse_number = _reject_json_number if reject_numbers else None
    value: Any = None
    parsing_failed = False
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
            **(
                {"parse_int": parse_number, "parse_float": parse_number}
                if parse_number is not None
                else {}
            ),
        )
    except (
        json.JSONDecodeError,
        RecursionError,
        _DuplicateObjectKey,
        _ForbiddenJsonNumber,
        _ForbiddenJsonConstant,
    ):
        parsing_failed = True
    if parsing_failed:
        _reject("invalid_json", 400)

    if type(value) is not dict or _has_invalid_json_string(value):
        _reject("invalid_json", 400)
    fields = set(value)
    if not required.issubset(fields) or not fields.issubset(allowed):
        _reject("invalid_json_fields", 400)
    return value


def require_exact_string(value: object, *, expected: str | None = None) -> str:
    """Require an exact string type, optionally with one exact value."""

    if expected is not None and type(expected) is not str:
        raise ValueError("invalid expected string configuration")
    if type(value) is not str or (expected is not None and value != expected):
        _reject("invalid_value", 400)
    return value


def require_bounded_utf8_string(
    value: object,
    *,
    maximum_bytes: int,
    allow_empty: bool = True,
) -> str:
    """Require a string whose strict UTF-8 representation fits a byte cap."""

    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 0
        or type(allow_empty) is not bool
    ):
        raise ValueError("invalid string limit configuration")
    result = require_exact_string(value)
    encoded: bytes | None = None
    try:
        encoded = result.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        pass
    if encoded is None:
        _reject("invalid_value", 400)
    if (not allow_empty and not result) or len(encoded) > maximum_bytes:
        _reject("invalid_value", 400)
    return result


def require_ascii_identifier(
    value: object,
    *,
    syntax: Pattern[str],
    maximum_bytes: int,
) -> str:
    """Require a caller-defined full-match syntax over bounded ASCII text."""

    if not isinstance(syntax, re.Pattern) or type(syntax.pattern) is not str:
        raise ValueError("invalid identifier syntax configuration")
    result = require_bounded_utf8_string(
        value,
        maximum_bytes=maximum_bytes,
        allow_empty=False,
    )
    if not result.isascii() or syntax.fullmatch(result) is None:
        _reject("invalid_value", 400)
    return result


def require_exact_empty_object(value: object) -> dict[str, Any]:
    """Require exactly an empty built-in JSON object."""

    if type(value) is not dict or value:
        _reject("invalid_value", 400)
    return value


__all__ = (
    "BoundaryError",
    "RawHeaderPair",
    "ValidatedHeaderPairs",
    "decode_strict_utf8",
    "get_security_header",
    "parse_json_object",
    "require_ascii_identifier",
    "require_bounded_body",
    "require_bounded_utf8_string",
    "require_exact_empty_object",
    "require_exact_string",
    "require_json_content_type",
    "require_method",
    "validate_raw_headers",
    "validate_security_headers",
)
