"""TEMPORARY read-only OWNER diagnostic for auth writer database targeting.

This endpoint never returns database URLs, hostnames, ports, database names,
usernames, credentials, SQL errors, or database data. It reuses the existing
production OWNER identity diagnostic as its authorization boundary and returns
only whether the configured reader and writer URLs target the same normalized
PostgreSQL endpoint and database.

Remove after the Team-invite provisioning incident is resolved.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import unquote, urlsplit

from api.auth import account_authority, http
from cuevion_auth import identity_inventory_diagnostic as owner_diagnostic


ROUTE = "/api/auth/writer-target-diagnostic"
_READER_ENV = "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL"
_WRITER_ENV = "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL"
_DEFAULT_POSTGRES_PORT = 5432


def _failure(status: int) -> http.PublicResponse:
    return http.json_response(
        status,
        {
            "error": {
                "code": "writer_target_diagnostic_unavailable",
                "message": "Writer target diagnostic is unavailable.",
            }
        },
    )


def _normalize_neon_hostname(hostname: str) -> str:
    lowered = hostname.lower()
    if not lowered.endswith(".neon.tech"):
        return lowered
    labels = lowered.split(".")
    if labels and labels[0].endswith("-pooler"):
        labels[0] = labels[0][:-len("-pooler")]
    return ".".join(labels)


def _target_from_env(name: str) -> tuple[str, int, str]:
    raw = os.environ[name]
    parsed_url = account_authority.parse_account_reader_database_url(
        {_READER_ENV: raw}
    )
    split = urlsplit(parsed_url.value)
    if split.hostname is None:
        raise RuntimeError("invalid target")
    port = split.port if split.port is not None else _DEFAULT_POSTGRES_PORT
    database = unquote(split.path[1:], encoding="utf-8", errors="strict")
    if not database:
        raise RuntimeError("invalid target")
    return (_normalize_neon_hostname(split.hostname), port, database)


def diagnostic_response(
    method: str,
    raw_headers: tuple[tuple[str, str], ...],
    path: str,
) -> http.PublicResponse:
    try:
        http.require_method(method, "GET")
        if path != ROUTE:
            return _failure(400)

        guard = owner_diagnostic.diagnostic_response(
            method,
            raw_headers,
            owner_diagnostic.ROUTE,
        )
        if guard.status != 200:
            return _failure(guard.status)

        same_target = _target_from_env(_READER_ENV) == _target_from_env(_WRITER_ENV)
        return http.json_response(
            200,
            {
                "temporaryWriterTargetDiagnostic": True,
                "writerTargetsSameDatabaseAsReader": same_target,
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
