"""Temporary insert-only initial OWNER migration; no schema changes.

All authority tables are locked in a fixed order before the READ COMMITTED
snapshot is read. This narrowly scoped, one-owner operation rejects phantoms
and concurrent provisioning as well as stale row versions. Ordinary reads
continue. There is no retry after an ambiguous commit: status is authoritative.
"""

from api.auth import account_authority, models
from cuevion_auth import account_record_ids
from cuevion_auth import current_account_repository_contract as contract
from cuevion_migration.owner_passkey import (
    FIELDS, MigrationDenied, require_envelope, require_target,
)
from cuevion_db import identity_inventory_diagnostic as inventory
from cuevion_db import postgresql_current_account_repository as canonical


_LOCK_SQL = """LOCK TABLE cuevion_account.users, cuevion_account.verified_emails,
cuevion_account.authentication_identities, cuevion_account.workspaces,
cuevion_account.workspace_memberships IN SHARE ROW EXCLUSIVE MODE"""


def _authority(cursor, issuer, subject, workspace_id):
    key = contract.AuthenticationIdentityLookupKey(issuer, subject)
    row = canonical._exact_result_row(cursor, canonical._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL,
        (issuer, subject, workspace_id), canonical._IDENTITY_RESULT_WIDTH)
    outcome, authority = canonical._decode_identity_authority(row, key, workspace_id)
    if outcome is not contract.CurrentAccountReadOutcome.FOUND or authority is None:
        raise MigrationDenied()
    result = contract.CurrentAccountAuthorityResult(outcome, authority)
    if not account_authority.auth0_authority_matches(result, key, authority.primary_verified_email.canonical_email):
        raise MigrationDenied()
    return authority


def _snapshot(cursor, session):
    authority = _authority(cursor, session.issuer, session.subject, session.workspace_id)
    tables = []
    for table, columns, decode, order in inventory._TABLES:
        cursor.execute(f"SELECT {', '.join(columns)} FROM cuevion_account.{table} ORDER BY {order} LIMIT 3")
        rows = cursor.fetchall()
        if type(rows) is not list or len(rows) > 2:
            raise MigrationDenied()
        tables.append(tuple(decode(row) for row in rows))
    snapshot = inventory.AccountSnapshot(authority, *tables)
    inventory._validate_snapshot(snapshot)
    return snapshot


class PostgreSQLOwnerPasskeyMigration:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def attach(self, expected, session, identity, *, now, revalidate):
        require_envelope(expected, session, adding=True)
        require_target(identity, expected)
        if type(now) is not int or now < max(i.created_at for i in expected.identities):
            raise MigrationDenied()
        connection = cursor = None
        try:
            connection = self._connection_factory()
            if connection.autocommit is not False:
                raise MigrationDenied()
            canonical._validate_initial_transaction_status(connection.info.transaction_status)
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET LOCAL statement_timeout = '5000ms'")
            cursor.execute("SET LOCAL lock_timeout = '1000ms'")
            cursor.execute(_LOCK_SQL)
            before = _snapshot(cursor, session)
            require_envelope(before, session, adding=True)
            if any(getattr(before, field) != getattr(expected, field) for field in FIELDS):
                raise MigrationDenied()
            # Includes disabled/historical claims, even if the global envelope
            # would independently have rejected them. Never rebind a subject.
            cursor.execute("SELECT identity_id FROM cuevion_account.authentication_identities "
                           "WHERE issuer = %s AND subject = %s LIMIT 1",
                           (identity.issuer, identity.subject))
            if cursor.fetchall() != []:
                raise MigrationDenied()
            revalidate()
            owner = before.authority
            new = models.AuthenticationIdentity(1,
                account_record_ids.generate_authentication_identity_id_candidate(),
                owner.user.user_id, identity.issuer, identity.subject, models.AuthenticationMethod.OIDC,
                models.AuthenticationIdentityStatus.ACTIVE, owner.primary_verified_email.email_id,
                now, None, 1)
            cursor.execute("INSERT INTO cuevion_account.authentication_identities "
                "(schema_version, identity_id, user_id, issuer, subject, authentication_method, status, "
                "verified_email_id, created_at, last_used_at, row_version) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (1, new.identity_id, new.user_id, new.issuer, new.subject, new.method.value,
                 new.status.value, new.verified_email_id, canonical._timestamp_to_database(now), None, 1))
            # The same SQL/decoder as ordinary login executes inside this write
            # transaction, so failed post-write authority proof rolls it back.
            resolved = _authority(cursor, new.issuer, new.subject, session.workspace_id)
            if (resolved.authentication_identity != new or resolved.user != owner.user
                    or resolved.primary_verified_email != owner.primary_verified_email
                    or resolved.workspace != owner.workspace
                    or resolved.workspace_membership != owner.workspace_membership):
                raise MigrationDenied()
            after = _snapshot(cursor, session)
            require_envelope(after, session)
            if (any(getattr(before, field) != getattr(after, field) for field in FIELDS if field != "identities")
                    or set(after.identities) != set(before.identities + (new,))):
                raise MigrationDenied()
            revalidate()
            connection.commit()
            return new
        except Exception:
            raise MigrationDenied() from None
        finally:
            # No state is repaired or retried if commit acknowledgment is lost.
            # Rollback on an idle committed connection is harmless.
            for target, action in ((cursor, "close"), (connection, "rollback"), (connection, "close")):
                if target is not None:
                    try:
                        getattr(target, action)()
                    except Exception:
                        pass


def build_runtime_repository(environment):
    writer = environment.get("CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL")
    if not writer or writer == environment.get("CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL"):
        raise MigrationDenied()
    parsed = account_authority.parse_account_reader_database_url(
        {"CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": writer})
    return PostgreSQLOwnerPasskeyMigration(account_authority.AccountReaderConnectionFactory(parsed))
