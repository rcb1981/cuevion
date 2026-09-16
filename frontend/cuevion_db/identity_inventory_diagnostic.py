"""TEMPORARY passkey-migration diagnostic. Remove with its HTTP route.

Uses the existing account-reader connection factory, SQL, and strict decoders.
Only the five tables already readable by that role are accessed. No writer,
operation/event-table grant, additional client, or migration is involved.
"""

from dataclasses import dataclass

from api.auth import account_authority, models, runtime
from cuevion_auth import current_account_repository_contract as contract
from cuevion_db import postgresql_current_account_repository as canonical


MAX_ACCOUNT_ROWS = 128  # Per table; exceeding the bound is a closed failure.


class InventoryUnavailable(Exception):
    def __init__(self):
        super().__init__("identity inventory unavailable")


class InventoryDenied(Exception):
    def __init__(self):
        super().__init__("identity inventory denied")


# Fixed identifiers only. No request-controlled SQL or identifier interpolation.
_TABLES = (
    ("users", canonical._USER_COLUMNS, canonical._decode_user, "user_id"),
    ("verified_emails", canonical._VERIFIED_EMAIL_COLUMNS,
     canonical._decode_verified_email, "email_id"),
    ("authentication_identities", canonical._AUTHENTICATION_IDENTITY_COLUMNS,
     canonical._decode_authentication_identity, "identity_id"),
    ("workspaces", canonical._WORKSPACE_COLUMNS,
     canonical._decode_workspace, "workspace_id"),
    ("workspace_memberships", canonical._WORKSPACE_MEMBERSHIP_COLUMNS,
     canonical._decode_workspace_membership, "workspace_id, user_id"),
)


@dataclass(frozen=True, repr=False)
class AccountSnapshot:
    authority: contract.CurrentAccountAuthority
    users: tuple
    emails: tuple
    identities: tuple
    workspaces: tuple
    memberships: tuple

    def __repr__(self):
        return "<TemporaryAccountSnapshot>"

    def counts(self):
        return {
            "users": len(self.users),
            "ownerMemberships": sum(m.role is models.WorkspaceRole.OWNER for m in self.memberships),
            "adminMemberships": sum(m.role is models.WorkspaceRole.ADMIN for m in self.memberships),
            "memberMemberships": sum(m.role is models.WorkspaceRole.MEMBER for m in self.memberships),
            "authenticationIdentities": len(self.identities),
            "activeWorkspaces": sum(w.status is models.WorkspaceStatus.ACTIVE for w in self.workspaces),
        }


def _unique(records, key):
    result = {}
    for record in records:
        identifier = key(record)
        if identifier in result:
            raise InventoryUnavailable()
        result[identifier] = record
    return result


def _validate_snapshot(snapshot):
    """Reject ambiguous or dangling graphs, including inactive records."""
    users = _unique(snapshot.users, lambda r: r.user_id)
    emails = _unique(snapshot.emails, lambda r: r.email_id)
    identities = _unique(snapshot.identities, lambda r: r.identity_id)
    workspaces = _unique(snapshot.workspaces, lambda r: r.workspace_id)
    memberships = _unique(snapshot.memberships, lambda r: (r.workspace_id, r.user_id))
    _unique(snapshot.identities, lambda r: (r.issuer, r.subject))
    _unique(tuple(e for e in snapshot.emails if e.status is models.VerifiedEmailStatus.VERIFIED),
            lambda r: r.canonical_email)
    for user in snapshot.users:
        if user.primary_verified_email_id is not None:
            email = emails.get(user.primary_verified_email_id)
            if email is None or email.user_id != user.user_id:
                raise InventoryUnavailable()
    for email in snapshot.emails:
        if email.user_id not in users:
            raise InventoryUnavailable()
    for identity in snapshot.identities:
        if identity.user_id not in users:
            raise InventoryUnavailable()
        if identity.verified_email_id is not None:
            email = emails.get(identity.verified_email_id)
            if email is None or email.user_id != identity.user_id:
                raise InventoryUnavailable()
    for workspace in snapshot.workspaces:
        if workspace.created_by_user_id not in users:
            raise InventoryUnavailable()
    for membership in snapshot.memberships:
        if membership.user_id not in users or membership.workspace_id not in workspaces:
            raise InventoryUnavailable()
    owner = snapshot.authority
    # The authority query and inventory must describe the identical snapshot.
    expected = (owner.user, owner.primary_verified_email, owner.authentication_identity,
                owner.workspace, owner.workspace_membership)
    actual = (users.get(owner.user.user_id), emails.get(owner.primary_verified_email.email_id),
              identities.get(owner.authentication_identity.identity_id),
              workspaces.get(owner.workspace.workspace_id),
              memberships.get((owner.workspace.workspace_id, owner.user.user_id)))
    if actual != expected:
        raise InventoryUnavailable()
    active_workspaces = [m for m in snapshot.memberships
        if m.user_id == owner.user.user_id
        and m.status is models.WorkspaceMembershipStatus.ACTIVE
        and workspaces[m.workspace_id].status is models.WorkspaceStatus.ACTIVE]
    if len(active_workspaces) != 1:
        raise InventoryUnavailable()


class PostgreSQLIdentityInventoryDiagnostic:
    """One bounded REPEATABLE READ READ ONLY snapshot, always rolled back."""

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def read(self, session):
        connection = cursor = None
        snapshot = None
        failure = None
        try:
            connection = self._connection_factory()
            if connection.autocommit is not False:
                raise InventoryUnavailable()
            canonical._validate_initial_transaction_status(connection.info.transaction_status)
            cursor = connection.cursor()
            cursor.execute(canonical._SET_TRANSACTION_SQL)
            cursor.execute("SET LOCAL statement_timeout = '5000ms'")
            cursor.execute("SET LOCAL lock_timeout = '1000ms'")
            key = contract.AuthenticationIdentityLookupKey(session.issuer, session.subject)
            row = canonical._exact_result_row(
                cursor, canonical._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL + " LIMIT 2",
                (key.issuer, key.subject, session.workspace_id), canonical._IDENTITY_RESULT_WIDTH,
            )
            outcome, authority = canonical._decode_identity_authority(row, key, session.workspace_id)
            if outcome is not contract.CurrentAccountReadOutcome.FOUND or authority is None:
                raise InventoryDenied()
            member = runtime._current_authority_member_context(session, authority)
            if member is None or member.membership_role != "owner":
                raise InventoryDenied()
            result = contract.CurrentAccountAuthorityResult(outcome, authority)
            if not account_authority.auth0_authority_matches(result, key, member.email):
                raise InventoryDenied()
            # An invite-provisioned MEMBER cannot become an initial OWNER through
            # a diagnostic; the ordinary callback also rejects this combination.
            if authority.primary_verified_email.verification_source.lower().startswith("team-invite-oidc"):
                raise InventoryDenied()
            tables = []
            for table, columns, decode, order in _TABLES:
                cursor.execute(
                    f"SELECT {', '.join(columns)} FROM cuevion_account.{table} "
                    f"ORDER BY {order} LIMIT {MAX_ACCOUNT_ROWS + 1}"
                )
                rows = cursor.fetchall()
                if type(rows) is not list or len(rows) > MAX_ACCOUNT_ROWS:
                    raise InventoryUnavailable()
                tables.append(tuple(decode(row) for row in rows))
            snapshot = AccountSnapshot(authority, *tables)
            _validate_snapshot(snapshot)
        except InventoryDenied:
            failure = InventoryDenied()
        except Exception:
            failure = InventoryUnavailable()
        finally:
            for target, action in ((cursor, "close"), (connection, "rollback"), (connection, "close")):
                if target is not None:
                    try:
                        getattr(target, action)()
                    except Exception:
                        failure = InventoryUnavailable()
        if failure is not None:
            raise failure from None
        if snapshot is None:
            raise InventoryUnavailable()
        return snapshot


def build_runtime_diagnostic_reader(environment):
    return PostgreSQLIdentityInventoryDiagnostic(account_authority.AccountReaderConnectionFactory(
        account_authority.parse_account_reader_database_url(environment)
    ))
