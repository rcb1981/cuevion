"""TEMPORARY read-only OWNER diagnostic for auth writer configuration.

This endpoint never returns database URLs or credentials. It first reuses the
existing production OWNER identity diagnostic as the authentication/authorization
boundary, then projects only four booleans about reader/writer configuration.
Remove after the Team-invite provisioning incident is resolved.
"""

from __future__ import annotations

import hmac
import os
from http.server import BaseHTTPRequestHandler

from api.auth import account_authority, http
from cuevion_auth import identity_inventory_diagnostic as owner_diagnostic


ROUTE = "/api/auth/writer-config-diagnostic"
_READER_ENV = "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL"
_WRITER_ENV = "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL"


def _failure(status: int) -> http.PublicResponse:
    return http.json_response(
        status,
        {
            "error": {
                "code": "writer_config_diagnostic_unavailable",
                "message": "Writer configuration diagnostic is unavailable.",
            }
        },
    )


def _configured(value: object) -> bool:
    return type(value) is str and bool(value)


def _writer_url_valid(value: object) -> bool:
    if not _configured(value):
        return False
    try:
        account_authority.parse_account_reader_database_url(
            {_READER_ENV: value}
        )
        return True
    except Exception:
        return False


def diagnostic_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    path: str,
) -> http.PublicResponse:
    try:
        http.require_method(method, "GET")
        if path != ROUTE:
            return _failure(400)

        # Reuse the existing production OWNER diagnostic boundary. Supplying its
        # fixed route here does not trust request input: this endpoint has already
        # required its own exact route above. The reused diagnostic still enforces
        # canonical host, same-origin Fetch Metadata, production environment,
        # authenticated server session, Auth0 issuer, and canonical OWNER access.
        guard = owner_diagnostic.diagnostic_response(
            method,
            raw_headers,
            owner_diagnostic.ROUTE,
        )
        if guard.status != 200:
            return _failure(guard.status)

        reader = os.environ.get(_READER_ENV)
        writer = os.environ.get(_WRITER_ENV)
        reader_configured = _configured(reader)
        writer_configured = _configured(writer)
        writer_equals_reader = (
            reader_configured
            and writer_configured
            and hmac.compare_digest(reader, writer)
        )

        return http.json_response(
            200,
            {
                "temporaryWriterConfigDiagnostic": True,
                "readerConfigured": reader_configured,
                "writerConfigured": writer_configured,
                "writerEqualsReader": writer_equals_reader,
                "writerUrlValid": _writer_url_valid(writer),
            },
        )
    except http.HttpBoundaryError as error:
        return _failure(error.status)
    except Exception:
        return _failure(503)


class handler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        try:
            raw_headers = http.snapshot_request_headers(self)
        except http.HttpBoundaryError:
            raw_headers = ()
        response = diagnostic_response(self.command, raw_headers, self.path)
        http.send_public_response(self, response)

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
