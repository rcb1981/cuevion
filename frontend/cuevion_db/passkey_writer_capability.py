"""Temporary writer capability probe: fixed SQL, no DML, rollback only."""

from api.auth import account_authority
from cuevion_db import postgresql_current_account_repository as canonical
from cuevion_db.postgresql_owner_passkey_migration import _LOCK_SQL


CLASSIFICATIONS = frozenset({
    "ready", "writer_configuration_missing", "writer_not_distinct",
    "writer_connection_failed", "writer_tls_invalid", "writer_select_privilege_missing",
    "writer_lock_privilege_missing", "writer_insert_privilege_missing",
    "owner_envelope_changed", "target_already_attached", "unavailable",
})
_TABLES = (
    ("users", "selectUsers"),
    ("verified_emails", "selectVerifiedEmails"),
    ("authentication_identities", "selectAuthenticationIdentities"),
    ("workspaces", "selectWorkspaces"),
    ("workspace_memberships", "selectWorkspaceMemberships"),
)
# The weaker preflight prevents even SELECT from queueing behind DDL. The
# final lock is the exact Phase 1 table list/order/mode, with NOWAIT added.
_READ_LOCK_SQL = _LOCK_SQL.replace("SHARE ROW EXCLUSIVE", "ACCESS SHARE") + " NOWAIT"
_EXACT_LOCK_SQL = _LOCK_SQL + " NOWAIT"
_INSERT_PRIVILEGE_SQL = (
    "SELECT pg_catalog.has_table_privilege("
    "'cuevion_account.authentication_identities', 'INSERT')"
)


def empty_result():
    return {"classification": "unavailable",
        **{key: False for key in (
            "writerDatabaseUrlConfigured", "readerDatabaseUrlConfigured", "writerDistinctFromReader",
            "writerConnectionEstablished", "tlsInUse", "autocommitFalse",
            "selectUsers", "selectVerifiedEmails", "selectAuthenticationIdentities",
            "selectWorkspaces", "selectWorkspaceMemberships", "exactPhase1LockAcquired",
            "insertIdentityPrivilege", "transactionRolledBack", "connectionClosed")}}


def _default_connect(parsed):
    # Use the same strict URL parser and connector as Phase 1, but inspect TLS
    # here so failures can be classified before our unconditional cleanup.
    return account_authority._default_connect(parsed.value, autocommit=False, connect_timeout=3)


def probe(environment, *, connect=None):
    result = empty_result()
    writer = environment.get("CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL")
    reader = environment.get("CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL")
    result["writerDatabaseUrlConfigured"] = type(writer) is str and bool(writer)
    result["readerDatabaseUrlConfigured"] = type(reader) is str and bool(reader)
    if not result["writerDatabaseUrlConfigured"] or not result["readerDatabaseUrlConfigured"]:
        result["classification"] = "writer_configuration_missing"
        return result
    result["writerDistinctFromReader"] = writer != reader
    if writer == reader:
        result["classification"] = "writer_not_distinct"
        return result
    try:
        parsed = account_authority.parse_account_reader_database_url(
            {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": writer})
    except Exception:
        result["classification"] = "writer_configuration_missing"
        return result
    connection = cursor = None
    stage = "connect"
    try:
        connection = (connect or _default_connect)(parsed)
        result["writerConnectionEstablished"] = True
        stage = "connection_properties"
        result["autocommitFalse"] = connection.autocommit is False
        result["tlsInUse"] = connection.pgconn.ssl_in_use is True
        if not result["tlsInUse"]:
            result["classification"] = "writer_tls_invalid"
        elif result["autocommitFalse"]:
            canonical._validate_initial_transaction_status(connection.info.transaction_status)
            cursor = connection.cursor()
            stage = "settings"
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET LOCAL statement_timeout = '1000ms'")
            cursor.execute("SET LOCAL lock_timeout = '200ms'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '2000ms'")
            stage = "select"
            cursor.execute(_READ_LOCK_SQL)
            for table, flag in _TABLES:
                cursor.execute(f"SELECT * FROM cuevion_account.{table} LIMIT 0")
                result[flag] = True
            stage = "insert_privilege"
            cursor.execute(_INSERT_PRIVILEGE_SQL)
            row = cursor.fetchone()
            if type(row) is not tuple or len(row) != 1 or type(row[0]) is not bool:
                raise ValueError("invalid privilege result")
            result["insertIdentityPrivilege"] = row[0]
            stage = "lock"
            cursor.execute(_EXACT_LOCK_SQL)
            result["exactPhase1LockAcquired"] = True
            result["classification"] = "ready" if row[0] else "writer_insert_privilege_missing"
            # No network work or extra SQL after the strong lock: rollback next.
    except Exception as error:
        from psycopg.errors import InsufficientPrivilege

        if stage == "connect":
            result["classification"] = "writer_connection_failed"
        elif isinstance(error, InsufficientPrivilege) and stage in ("select", "lock"):
            result["classification"] = (
                "writer_select_privilege_missing" if stage == "select" else "writer_lock_privilege_missing")
        else:
            # Lock contention/timeouts, missing relations, and unexpected driver
            # errors are not evidence of missing privileges. No error text escapes.
            result["classification"] = "unavailable"
    finally:
        if connection is not None:
            try:
                connection.rollback()
                result["transactionRolledBack"] = True
            except Exception:
                result["classification"] = "unavailable"
            finally:
                try:
                    if cursor is not None:
                        cursor.close()
                except Exception:
                    result["classification"] = "unavailable"
                finally:
                    try:
                        connection.close()
                        result["connectionClosed"] = True
                    except Exception:
                        result["classification"] = "unavailable"
                        # If the high-level close failed, still attempt native
                        # socket teardown. Never report readiness on cleanup failure.
                        try:
                            connection.pgconn.finish()
                            result["connectionClosed"] = True
                        except Exception:
                            pass
    return result
