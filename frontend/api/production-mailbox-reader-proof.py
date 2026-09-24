"""Authenticated Production-only reader connectivity proof.

This route never reads mailbox rows and never enables Gmail cache authority.
It performs one bounded read-only database identity probe while the runtime is
explicitly in production_probe mode.
"""

from http.server import BaseHTTPRequestHandler
import os

from api.auth import http as auth_http
from api.auth import runtime as auth_runtime
from cuevion_mailbox.preview_active_read import gmail_cache_authority_enabled
from cuevion_mailbox.runtime import (
    production_reader_probe_enabled,
    run_production_reader_connectivity_probe,
)


def _json(status: int, payload: dict, *, set_cookies=()):
    return auth_http.json_response(
        status,
        payload,
        set_cookies=set_cookies,
    )


def production_reader_probe_response(handler) -> auth_http.PublicResponse:
    try:
        auth_http.require_method(handler.command, "GET")
        headers = auth_http.snapshot_request_headers(handler)
        auth_http.require_canonical_host(headers)
    except auth_http.HttpBoundaryError as error:
        return _json(
            error.status,
            {"ok": False, "error": {"code": error.code}},
        )
    except Exception:
        return _json(
            503,
            {"ok": False, "error": {"code": "probe_unavailable"}},
        )

    resolution = auth_runtime.resolve_authenticated_member(headers)
    if resolution.outcome is auth_runtime.MemberResolutionOutcome.UNAUTHENTICATED:
        return _json(
            401,
            {"ok": False, "error": {"code": "authentication_required"}},
            set_cookies=resolution.set_cookies,
        )
    if (
        resolution.outcome is auth_runtime.MemberResolutionOutcome.UNAVAILABLE
        or resolution.member is None
    ):
        return _json(
            503,
            {"ok": False, "error": {"code": "authentication_unavailable"}},
            set_cookies=resolution.set_cookies,
        )

    if not production_reader_probe_enabled(os.environ):
        return _json(
            404,
            {"ok": False, "error": {"code": "not_found"}},
            set_cookies=resolution.set_cookies,
        )

    if gmail_cache_authority_enabled(os.environ):
        return _json(
            503,
            {"ok": False, "error": {"code": "probe_gate_conflict"}},
            set_cookies=resolution.set_cookies,
        )

    try:
        result = run_production_reader_connectivity_probe(os.environ)
    except Exception:
        return _json(
            503,
            {"ok": False, "error": {"code": "mailbox_reader_probe_unavailable"}},
            set_cookies=resolution.set_cookies,
        )

    return _json(
        200,
        {
            "ok": True,
            "mode": "production_probe",
            "reader": result.status,
            "transaction_read_only": True,
            "cache_authority_enabled": False,
        },
        set_cookies=resolution.set_cookies,
    )


class handler(BaseHTTPRequestHandler):
    def _respond(self):
        auth_http.send_public_response(
            self,
            production_reader_probe_response(self),
        )

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond
    do_PATCH = _respond
    do_DELETE = _respond
    do_OPTIONS = _respond
    do_HEAD = _respond
    do_TRACE = _respond
    do_CONNECT = _respond

    def log_message(self, _format, *_args):
        return
