from __future__ import annotations

if __name__ != "api.collaboration.http_adapter":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.http_adapter"
    )

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .http_boundary import (
    BoundaryError,
    ValidatedHeaderPairs,
    decode_strict_utf8,
    get_security_header,
    parse_json_object,
    require_bounded_body,
    require_json_content_type,
    require_method,
    validate_security_headers,
)


HTTP_MODE_ENVIRONMENT_NAME = "CUEVION_COLLAB_V2_HTTP_MODE"
HTTP_MODE_OFF = "off"
HTTP_MODES = frozenset(
    {HTTP_MODE_OFF, "owner_read", "owner_write", "guest", "frontend"}
)
PUBLIC_JSON_MAXIMUM_DEPTH = 16

_CANONICAL_CONTENT_LENGTH_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_PUBLIC_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LIST_ITERATOR_TYPE = type(iter([]))
_BOUNDARY_PUBLIC_ERRORS = {
    ("invalid_headers", 400): ("invalid_request", 400),
    ("ambiguous_headers", 400): ("invalid_request", 400),
    ("invalid_framing", 400): ("invalid_request", 400),
    ("missing_header", 400): ("invalid_request", 400),
    ("invalid_utf8", 400): ("invalid_request", 400),
    ("invalid_json", 400): ("invalid_request", 400),
    ("invalid_json_fields", 400): ("invalid_request", 400),
    ("invalid_value", 400): ("invalid_request", 400),
    ("method_not_allowed", 405): ("method_not_allowed", 405),
    ("payload_too_large", 413): ("payload_too_large", 413),
    ("unsupported_content_type", 415): ("unsupported_media_type", 415),
}


class RouteDisabled(Exception):
    """An adapter-safe, generic not-found outcome for an inactive route."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("not_found")


@dataclass(frozen=True, slots=True)
class PublicResponse:
    """An immutable response ready for a BaseHTTPRequestHandler interface."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def parse_http_mode(value: object = None) -> str:
    """Return one exact configured mode, failing closed to ``off``."""

    if type(value) is str and value in HTTP_MODES:
        return value
    return HTTP_MODE_OFF


def parse_http_mode_mapping(values: object) -> str:
    """Read the HTTP mode from an explicitly supplied mapping only."""

    if not isinstance(values, Mapping):
        return HTTP_MODE_OFF
    try:
        value = values.get(HTTP_MODE_ENVIRONMENT_NAME)
    except Exception:
        return HTTP_MODE_OFF
    return parse_http_mode(value)


def _validated_allowed_modes(allowed_modes: object) -> frozenset[str]:
    if type(allowed_modes) not in (set, frozenset, list, tuple):
        raise ValueError("invalid allowed HTTP modes configuration")
    if not allowed_modes:
        raise ValueError("invalid allowed HTTP modes configuration")
    for mode in allowed_modes:
        if (
            type(mode) is not str
            or mode not in HTTP_MODES
            or mode == HTTP_MODE_OFF
        ):
            raise ValueError("invalid allowed HTTP modes configuration")
    return frozenset(allowed_modes)


def require_enabled_http_mode(
    value: object,
    *,
    allowed_modes: object,
) -> str:
    """Require an exact active mode without inferring any mode ordering."""

    allowed = _validated_allowed_modes(allowed_modes)
    mode = parse_http_mode(value)
    if mode not in allowed:
        raise RouteDisabled
    return mode


def extract_raw_headers(request: object) -> ValidatedHeaderPairs:
    """Snapshot ``headers.raw_items()`` while preserving order and duplicates."""

    try:
        headers = request.headers  # type: ignore[attr-defined]
        raw_items = headers.raw_items
    except Exception:
        raise BoundaryError("invalid_headers", 400) from None
    if not callable(raw_items):
        raise BoundaryError("invalid_headers", 400)

    try:
        raw_result = raw_items()
        if type(raw_result) not in (list, tuple, _LIST_ITERATOR_TYPE):
            raise TypeError
        snapshot = list(raw_result)
    except Exception:
        raise BoundaryError("invalid_headers", 400) from None

    return validate_security_headers(snapshot)


def _validate_maximum_bytes(maximum_bytes: object) -> int:
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ValueError("invalid body limit configuration")
    return maximum_bytes


def preflight_content_length(
    headers: object,
    *,
    maximum_bytes: int,
    required: bool = True,
) -> int | None:
    """Validate Content-Length text against a trusted cap before allocation."""

    maximum = _validate_maximum_bytes(maximum_bytes)
    if type(required) is not bool:
        raise ValueError("invalid Content-Length configuration")
    validated = validate_security_headers(headers)
    content_length = get_security_header(validated, "content-length")
    if content_length is None:
        if required:
            raise BoundaryError("invalid_framing", 400)
        return None
    if (
        not content_length.isascii()
        or _CANONICAL_CONTENT_LENGTH_RE.fullmatch(content_length) is None
    ):
        raise BoundaryError("invalid_framing", 400)

    maximum_text = str(maximum)
    if (
        len(content_length) > len(maximum_text)
        or (
            len(content_length) == len(maximum_text)
            and content_length > maximum_text
        )
    ):
        raise BoundaryError("payload_too_large", 413)
    return int(content_length)


def read_json_object(
    request: object,
    *,
    maximum_bytes: int,
    allowed_fields: object,
    required_fields: object = (),
) -> dict[str, Any]:
    """Read exactly one bounded JSON body and enforce its top-level schema."""

    headers = extract_raw_headers(request)
    content_length = preflight_content_length(
        headers,
        maximum_bytes=maximum_bytes,
        required=True,
    )
    require_json_content_type(headers)
    assert content_length is not None

    try:
        read = request.rfile.read  # type: ignore[attr-defined]
    except Exception:
        raise BoundaryError("invalid_framing", 400) from None
    if not callable(read):
        raise BoundaryError("invalid_framing", 400)

    body = read(content_length)
    if type(body) is not bytes:
        raise BoundaryError("invalid_framing", 400)
    bounded = require_bounded_body(
        headers,
        body,
        maximum_bytes=maximum_bytes,
        require_content_length=True,
    )
    decoded = decode_strict_utf8(bounded)
    return parse_json_object(
        decoded,
        allowed_fields=allowed_fields,
        required_fields=required_fields,
    )


def validate_no_body_request(
    request: object,
    *,
    supplied_body: object = b"",
) -> ValidatedHeaderPairs:
    """Validate a no-body request without inspecting or consuming its stream."""

    if type(supplied_body) is not bytes or supplied_body:
        raise BoundaryError("invalid_framing", 400)
    headers = extract_raw_headers(request)
    try:
        content_length = preflight_content_length(
            headers,
            maximum_bytes=0,
            required=False,
        )
    except BoundaryError as error:
        if error.code == "payload_too_large":
            raise BoundaryError("invalid_framing", 400) from None
        raise
    if content_length not in (None, 0):
        raise BoundaryError("invalid_framing", 400)
    return headers


def require_request_method(method: object, *, expected_method: str) -> str:
    """Require one exact method through the shared pure boundary."""

    return require_method(method, expected_method)


def _validate_status(status: object, *, success: bool) -> int:
    if type(status) is not int:
        raise ValueError("invalid public response status")
    if success:
        if status not in (200, 201):
            raise ValueError("invalid public success status")
    elif not 400 <= status <= 599:
        raise ValueError("invalid public failure status")
    return status


def _validate_public_error_code(code: object) -> str:
    if (
        type(code) is not str
        or not code.isascii()
        or _PUBLIC_ERROR_CODE_RE.fullmatch(code) is None
    ):
        raise ValueError("invalid public error code configuration")
    return code


def _json_response(status: int, body: bytes) -> PublicResponse:
    headers = (
        ("Content-Type", "application/json; charset=utf-8"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Content-Length", str(len(body))),
    )
    return PublicResponse(status=status, headers=headers, body=body)


def _invalid_public_success_data() -> None:
    raise ValueError("invalid public success data")


def _copy_canonical_public_json_value(
    value: object,
    *,
    depth: int,
    active_container_ids: set[int],
) -> Any:
    value_type = type(value)
    if value_type is dict:
        if depth > PUBLIC_JSON_MAXIMUM_DEPTH:
            _invalid_public_success_data()
        container_id = id(value)
        if container_id in active_container_ids:
            _invalid_public_success_data()
        active_container_ids.add(container_id)
        copied_dict: dict[str, Any] = {}
        try:
            for key, item in value.items():
                if type(key) is not str:
                    _invalid_public_success_data()
                copied_dict[key] = _copy_canonical_public_json_value(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                )
        finally:
            active_container_ids.remove(container_id)
        return copied_dict
    if value_type is list:
        if depth > PUBLIC_JSON_MAXIMUM_DEPTH:
            _invalid_public_success_data()
        container_id = id(value)
        if container_id in active_container_ids:
            _invalid_public_success_data()
        active_container_ids.add(container_id)
        copied_list: list[Any] = []
        try:
            for item in value:
                copied_list.append(
                    _copy_canonical_public_json_value(
                        item,
                        depth=depth + 1,
                        active_container_ids=active_container_ids,
                    )
                )
        finally:
            active_container_ids.remove(container_id)
        return copied_list
    if value_type is str:
        return value
    if value_type is bool:
        return value
    if value_type is int:
        return value
    if value_type is float:
        if not math.isfinite(value):
            _invalid_public_success_data()
        return value
    if value is None:
        return None
    _invalid_public_success_data()


def json_success(data: object, *, status: int = 200) -> PublicResponse:
    """Build deterministic compact JSON without accepting a non-object DTO."""

    validated_status = _validate_status(status, success=True)
    if type(data) is not dict:
        raise ValueError("invalid public success data")
    canonical_data = _copy_canonical_public_json_value(
        data,
        depth=0,
        active_container_ids=set(),
    )
    try:
        encoded_data = json.dumps(
            canonical_data,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ValueError("invalid public success data") from None
    body = b'{"ok":true,"data":' + encoded_data + b"}"
    return _json_response(validated_status, body)


def json_failure(code: object, *, status: int) -> PublicResponse:
    """Build a failure from one trusted public code, never an error object."""

    validated_status = _validate_status(status, success=False)
    validated_code = _validate_public_error_code(code)
    body = (
        b'{"ok":false,"error":{"code":'
        + json.dumps(validated_code).encode("ascii")
        + b"}}"
    )
    return _json_response(validated_status, body)


def empty_success() -> PublicResponse:
    """Build the sole supported empty response: HTTP 204."""

    return PublicResponse(
        status=204,
        headers=(
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Length", "0"),
        ),
        body=b"",
    )


def _validate_allow_method(allow_method: object) -> str:
    if (
        type(allow_method) is not str
        or not allow_method.isascii()
        or allow_method != allow_method.upper()
        or _HTTP_TOKEN_RE.fullmatch(allow_method) is None
    ):
        raise ValueError("invalid Allow configuration")
    return allow_method


def normalize_boundary_error(
    error: BoundaryError,
    *,
    allow_method: str | None = None,
) -> PublicResponse:
    """Map internal boundary details onto the approved public error families."""

    if type(error) is not BoundaryError:
        raise ValueError("invalid boundary error")
    validated_allow = (
        _validate_allow_method(allow_method)
        if allow_method is not None
        else None
    )

    if type(error.code) is not str or type(error.status) is not int:
        return json_failure("internal_error", status=500)
    public_error = _BOUNDARY_PUBLIC_ERRORS.get((error.code, error.status))
    if public_error is None:
        return json_failure("internal_error", status=500)
    public_code, public_status = public_error
    if public_status == 405:
        if validated_allow is None:
            raise ValueError("missing Allow configuration")
        response = json_failure(public_code, status=public_status)
        return PublicResponse(
            status=response.status,
            headers=response.headers + (("Allow", validated_allow),),
            body=response.body,
        )
    return json_failure(public_code, status=public_status)


def _validate_public_response(response: object) -> PublicResponse:
    if type(response) is not PublicResponse:
        raise ValueError("invalid public response")
    if (
        type(response.status) is not int
        or type(response.headers) is not tuple
        or type(response.body) is not bytes
    ):
        raise ValueError("invalid public response")
    for pair in response.headers:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
        ):
            raise ValueError("invalid public response")

    names = [name.lower() for name, _value in response.headers]
    if len(names) != len(set(names)) or any(
        name.startswith("access-control-") for name in names
    ):
        raise ValueError("invalid public response")
    if response.status == 204:
        if response != empty_success():
            raise ValueError("invalid public response")
    else:
        if not response.body or response.status < 200 or response.status > 599:
            raise ValueError("invalid public response")
        try:
            parsed = json.loads(response.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("invalid public response")
        if (
            type(parsed) is dict
            and set(parsed) == {"ok", "data"}
            and parsed.get("ok") is True
            and type(parsed.get("data")) is dict
        ):
            expected = json_success(parsed["data"], status=response.status)
        elif (
            type(parsed) is dict
            and set(parsed) == {"ok", "error"}
            and parsed.get("ok") is False
            and type(parsed.get("error")) is dict
            and set(parsed["error"]) == {"code"}
        ):
            expected = json_failure(
                parsed["error"]["code"],
                status=response.status,
            )
        else:
            raise ValueError("invalid public response")
        expected_headers = expected.headers
        if response.status == 405:
            if len(response.headers) != len(expected_headers) + 1:
                raise ValueError("invalid public response")
            allow_name, allow_value = response.headers[-1]
            if allow_name != "Allow":
                raise ValueError("invalid public response")
            _validate_allow_method(allow_value)
            expected_headers += (("Allow", allow_value),)
        if response.headers != expected_headers or response.body != expected.body:
            raise ValueError("invalid public response")
    return response


def invoke_safely(
    callback: Callable[[], PublicResponse],
    *,
    allow_method: str | None = None,
) -> PublicResponse:
    """Invoke once and normalize expected request and unexpected runtime errors."""

    if not callable(callback):
        raise ValueError("invalid callback configuration")
    if allow_method is not None:
        _validate_allow_method(allow_method)
    try:
        response = callback()
        return _validate_public_response(response)
    except RouteDisabled:
        return json_failure("not_found", status=404)
    except BoundaryError as error:
        try:
            return normalize_boundary_error(error, allow_method=allow_method)
        except Exception:
            return json_failure("internal_error", status=500)
    except Exception:
        return json_failure("internal_error", status=500)


def invoke_if_http_mode(
    value: object,
    *,
    allowed_modes: object,
    callback: Callable[[], PublicResponse],
    allow_method: str | None = None,
) -> PublicResponse:
    """Gate before callback execution so disabled routes cannot read or import."""

    allowed = _validated_allowed_modes(allowed_modes)
    if not callable(callback):
        raise ValueError("invalid callback configuration")
    if allow_method is not None:
        _validate_allow_method(allow_method)
    try:
        require_enabled_http_mode(value, allowed_modes=allowed)
    except RouteDisabled:
        return json_failure("not_found", status=404)
    return invoke_safely(callback, allow_method=allow_method)


def write_public_response(request: object, response: PublicResponse) -> None:
    """Write one validated response through the standard handler interface."""

    validated = _validate_public_response(response)
    try:
        send_response = request.send_response  # type: ignore[attr-defined]
        send_header = request.send_header  # type: ignore[attr-defined]
        end_headers = request.end_headers  # type: ignore[attr-defined]
        write = request.wfile.write  # type: ignore[attr-defined]
    except Exception:
        raise ValueError("invalid response writer interface") from None
    if not all(callable(item) for item in (send_response, send_header, end_headers, write)):
        raise ValueError("invalid response writer interface")

    send_response(validated.status)
    for name, value in validated.headers:
        send_header(name, value)
    end_headers()
    if validated.status != 204:
        write(validated.body)


__all__ = (
    "HTTP_MODE_ENVIRONMENT_NAME",
    "HTTP_MODE_OFF",
    "HTTP_MODES",
    "PUBLIC_JSON_MAXIMUM_DEPTH",
    "PublicResponse",
    "RouteDisabled",
    "empty_success",
    "extract_raw_headers",
    "invoke_if_http_mode",
    "invoke_safely",
    "json_failure",
    "json_success",
    "normalize_boundary_error",
    "parse_http_mode",
    "parse_http_mode_mapping",
    "preflight_content_length",
    "read_json_object",
    "require_enabled_http_mode",
    "require_request_method",
    "validate_no_body_request",
    "write_public_response",
)
