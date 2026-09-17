"""TEMPORARY read-only OWNER diagnostic for auth writer connectivity/capability.

This endpoint never returns database URLs, usernames, credentials, SQL errors,
or database-derived values other than fixed booleans. It reuses the existing
production OWNER identity diagnostic as its authorization boundary, opens the
configured dedicated auth writer connection, inspects only connection state and
PostgreSQL privilege predicates, then rolls the transaction back.

Remove after the Team-invite provisioning incident is resolved.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler

from api.auth import account_authority, http
from cuevion_auth import identity_inventory_diagnostic as owner_diagnostic


ROUTE = "/api/auth/writer-capability-diagnostic"
_READER_ENV = "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL"
_WRITER_ENV = "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL"
_EXPECTED_WRITER_ROLE = "cuevion_auth_writer"

_PRIVILEGE_SQL = """
SELECT
    current_user = 'cuevion_auth_writer',
    has_schema_privilege(current_user, 'cuevion_account', 'USAGE'),
    has_table_privilege(current_user, 'cuevion_account.workspaces', 'SELECT')
      AND has_table_privilege(current_user, 'cuevion_account.users', 'SELECT')
      AND has_table_privilege(current_user, 'cuevion_account.verified_emails', 'SELECT')
      AND has_table_privilege(current_user, 'cuevion_account.authentication_identities', 'SELECT')
      AND has_table_privilege(current_user, 'cuevion_account.workspace_memberships', 'SELECT'),
    has_table_privilege(current_user, 'cuevion_account.users', 'INSERT')
      AND has_table_privilege(current_user, 'cuevion_account.verified_emails', 'INSERT')
      AND has_table_privilege(current_user, 'cuevion_account.authentication_identities', 'INSERT')
      AND has_table_privilege(current_user, 'cuevion_account.workspace_memberships', 'INSERT'),
    has_table_privilege(current_user, 'cuevion_account.workspace_memberships', 'UPDATE'),
    has_function_privilege(current_user, 'pg_catalog.pg_advisory_xact_lock(bigint)', 'EXECUTE')
""".strip()


def _failure(status: int) -> http.PublicResponse:
    return http.json_response(
        status,
        {
            "error": {
                "code": "writer_capability_diagnostic_unavailable",
                "message": "Writer capability diagnostic is unavailable.",
            }
        },
    )


def _default_result() -> dict[str, bool]:
    return {
        "writerConnects": False,
        "writerTlsActive": False,
        "writerTransactionReady": False,
        "writerRoleIsCuevionAuthWriter": False,
        "writerSchemaUsage": False,
        "writerRequiredSelectPrivileges": False,
        "writerRequiredInsertPrivileges": False,
        "writerMembershipUpdatePrivilege": False,
        "writerAdvisoryLockPrivilege": False,
    }


def _inspect_writer() -> dict[str, bool]:
    result = _default_result()
    connection = None
    cursor = None
    try:
        writer_url = os.environ[_WRITER_ENV]
        parsed = account_authority.parse_account_reader_database_url(
            {_READER_ENV: writer_url}
        )
        factory = account_authority.AccountReaderConnectionFactory(parsed)
        connection = factory()
        result["writerConnects"] = True
        result["writerTlsActive"] = getattr(connection.pgconn, "ssl_in_use") is True
        transaction_status = getattr(connection.info, "transaction_status")
        result["writerTransactionReady"] = (
            not isinstance(transaction_status, bool) and transaction_status == 0
        )
        if not result["writerTransactionReady"]:
            return result

        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5000ms'")
        cursor.execute("SET LOCAL lock_timeout = '2000ms'")
        cursor.execute(_PRIVILEGE_SQL)
        rows = cursor.fetchall()
        if (
            type(rows) is not list
            or len(rows) != 1
            or type(rows[0]) is not tuple
            or len(rows[0]) != 6
            or any(type(value) is not bool for value in rows[0])
        ):
            return result
        (
            result["writerRoleIsCuevionAuthWriter"],
            result["writerSchemaUsage"],
            result["writerRequiredSelectPrivileges"],
            result["writerRequiredInsertPrivileges"],
            result["writerMembershipUpdatePrivilege"],
            result["writerAdvisoryLockPrivilege"],
        ) = rows[0]
        return result
    except Exception:
        return result
    finally:
        for target, method_name in (
            (cursor, "close"),
            (connection, "rollback"),
            (connection, "close"),
        ):
            if target is None:
                continue
            try:
                getattr(target, method_name)()
            except Exception:
                pass


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

        result = _inspect_writer()
        return http.json_response(
            200,
            {
                "temporaryWriterCapabilityDiagnostic": True,
                **result,
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
