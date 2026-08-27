"""Strict HTTP adapter helpers for the semantic Priority route."""

from __future__ import annotations

import json

from api.auth import http as auth_http


MAX_REQUEST_BODY_BYTES = 64 * 1_024


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise ValueError("invalid JSON constant")


def read_semantic_json_request(handler) -> tuple[tuple[tuple[str, str], ...], dict]:
    headers = auth_http.snapshot_request_headers(handler)
    auth_http.require_method(getattr(handler, "command", None), "POST")
    auth_http.require_canonical_host(headers)
    auth_http.require_same_origin(headers)
    transfer_encoding = auth_http.get_unique_header(headers, "transfer-encoding")
    if transfer_encoding is not None:
        raise auth_http.HttpBoundaryError("invalid_request", 400)
    content_type = auth_http.get_unique_header(headers, "content-type", required=True)
    if content_type not in {"application/json", "application/json; charset=utf-8"}:
        raise auth_http.HttpBoundaryError("invalid_request", 400)
    raw_length = auth_http.get_unique_header(headers, "content-length", required=True)
    if (
        raw_length is None
        or not raw_length.isascii()
        or not raw_length.isdigit()
        or len(raw_length) > 10
    ):
        raise auth_http.HttpBoundaryError("invalid_request", 400)
    content_length = int(raw_length)
    if not 1 <= content_length <= MAX_REQUEST_BODY_BYTES:
        raise auth_http.HttpBoundaryError("invalid_request", 413 if content_length > MAX_REQUEST_BODY_BYTES else 400)
    try:
        raw_body = handler.rfile.read(content_length)
    except Exception:
        raise auth_http.HttpBoundaryError("invalid_request", 400) from None
    if type(raw_body) is not bytes or len(raw_body) != content_length:
        raise auth_http.HttpBoundaryError("invalid_request", 400)
    try:
        payload = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise auth_http.HttpBoundaryError("invalid_request", 400) from None
    if type(payload) is not dict:
        raise auth_http.HttpBoundaryError("invalid_request", 400)
    return headers, payload


def read_workflow_json_request(handler) -> tuple[tuple[tuple[str, str], ...], dict]:
    """Read the dormant workflow endpoint through the same strict boundary."""

    return read_semantic_json_request(handler)


def send_semantic_json(handler, status: int, payload: dict, *, retry_after: int | None = None) -> None:
    extra_headers = ()
    if retry_after is not None:
        if type(retry_after) is not int or not 1 <= retry_after <= 86_400:
            raise ValueError("invalid trusted retry interval")
        extra_headers = (("Retry-After", str(retry_after)),)
    response = auth_http.json_response(
        status,
        payload,
        extra_headers=extra_headers,
    )
    auth_http.send_public_response(handler, response)


def send_workflow_json(handler, status: int, payload: dict) -> None:
    send_semantic_json(handler, status, payload)


def send_http_boundary_error(handler, error: auth_http.HttpBoundaryError) -> None:
    code = error.code
    message = {
        "ambiguous_headers": "Request headers are ambiguous.",
        "forbidden_host": "Request host is not permitted.",
        "forbidden_origin": "Request origin is not permitted.",
        "method_not_allowed": "Use POST for semantic assessment.",
    }.get(code, "Request is invalid.")
    extra = (("Allow", "POST"),) if code == "method_not_allowed" else ()
    auth_http.send_public_response(
        handler,
        auth_http.json_response(
            error.status,
            {"ok": False, "error": {"code": code, "message": message}},
            extra_headers=extra,
        ),
    )


def send_workflow_http_boundary_error(
    handler,
    error: auth_http.HttpBoundaryError,
) -> None:
    code = error.code
    message = {
        "ambiguous_headers": "Request headers are ambiguous.",
        "forbidden_host": "Request host is not permitted.",
        "forbidden_origin": "Request origin is not permitted.",
        "method_not_allowed": "Use POST for Priority workflow authority.",
    }.get(code, "Request is invalid.")
    extra = (("Allow", "POST"),) if code == "method_not_allowed" else ()
    auth_http.send_public_response(
        handler,
        auth_http.json_response(
            error.status,
            {"ok": False, "error": {"code": code, "message": message}},
            extra_headers=extra,
        ),
    )
