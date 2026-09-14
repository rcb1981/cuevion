"""Invite-bound account writes using the existing schema-one base tables.

The caller supplies verified identity and current invitation evidence. PREPARE
does not grant account access. FINALIZE may be called only after freshly proving
the exact Redis invitation acceptance. No initial-owner operation or event is
created, and this module owns no credentials, clock, or provider integration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from api.auth import models


class ProvisioningConflict(Exception):
    def __init__(self) -> None:
        super().__init__("team invitee provisioning conflict")


class ProvisioningUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("team invitee provisioning unavailable")


def _fail(error_type: type[Exception]) -> None:
    error = error_type()
    try:
        raise error
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class InviteePreparationRequest:
    issuer: str
    subject: str
    email: str
    workspace_id: str
    invitation_id: str
    token_digest: str
    inviter_user_id: str

    def __post_init__(self) -> None:
        if type(self.email) is str:
            object.__setattr__(self, "email", self.email.strip().lower())
        _validate_request(self)

    def __repr__(self) -> str:
        return "InviteePreparationRequest(...)"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedTeamInvitee:
    user_id: str
    email_id: str
    identity_id: str
    issuer: str
    subject: str
    email: str
    workspace_id: str
    invitation_id: str
    token_digest: str
    inviter_user_id: str
    created_at: int
    membership_row_version: int
    status: str
    security_epoch: int

    def __post_init__(self) -> None:
        request = _prepared_request(self)
        if (
            any(type(value) is not str for value in
                (self.user_id, self.email_id, self.identity_id))
            or (self.user_id, self.email_id, self.identity_id) != _ids(request)
            or self.email != request.email
            or not _timestamp(self.created_at)
            or type(self.membership_row_version) is not int
            or type(self.status) is not str
            or (self.status, self.membership_row_version)
            not in (("suspended", 1), ("active", 2))
            or type(self.security_epoch) is not int
            or self.security_epoch != 1
        ):
            _fail(ProvisioningConflict)

    def __repr__(self) -> str:
        return "PreparedTeamInvitee(...)"


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SOURCE = "team-invite-oidc"
_DISPLAY_NAME = "Team member"
_COLUMNS = {
    "users": (
        "schema_version", "user_id", "status", "primary_verified_email_id",
        "display_name", "security_epoch", "created_at", "updated_at", "row_version",
    ),
    "verified_emails": (
        "schema_version", "email_id", "user_id", "canonical_email", "status",
        "verification_source", "created_at", "verified_at", "retired_at", "row_version",
    ),
    "authentication_identities": (
        "schema_version", "identity_id", "user_id", "issuer", "subject",
        "authentication_method", "status", "verified_email_id", "created_at",
        "last_used_at", "row_version",
    ),
    "workspace_memberships": (
        "schema_version", "workspace_id", "user_id", "role", "status",
        "created_at", "updated_at", "row_version",
    ),
}
_SELECTS = {
    "users": "user_id = %s",
    "verified_emails": (
        "(canonical_email = %s AND status = 'verified' AND retired_at IS NULL) "
        "OR email_id = %s OR user_id = %s"
    ),
    "authentication_identities": (
        "(issuer = %s AND subject = %s) OR identity_id = %s OR user_id = %s"
    ),
    "workspace_memberships": "user_id = %s",
}
_OWNER_SQL = """
SELECT w.schema_version, w.workspace_id, w.status, w.row_version,
       u.schema_version, u.user_id, u.status, u.row_version, u.security_epoch,
       m.schema_version, m.workspace_id, m.user_id, m.role, m.status, m.row_version
FROM cuevion_account.workspaces w
JOIN cuevion_account.workspace_memberships m ON m.workspace_id = w.workspace_id
JOIN cuevion_account.users u ON u.user_id = m.user_id
WHERE w.workspace_id = %s AND u.user_id = %s
FOR SHARE OF w, u, m
"""
_FINALIZE_SQL = """
UPDATE cuevion_account.workspace_memberships
SET status = 'active', updated_at = %s, row_version = 2
WHERE workspace_id = %s AND user_id = %s AND schema_version = 1
  AND role = 'member' AND status = 'suspended' AND row_version = 1
  AND created_at = %s AND updated_at = %s
RETURNING row_version
"""


def _timestamp(value: object) -> bool:
    return type(value) is int and 0 <= value <= models.MAX_UNIX_UTC_SECONDS


def _ids(request: InviteePreparationRequest) -> tuple[str, str, str]:
    def record_id(prefix: str, domain: str, values: tuple[str, ...]) -> str:
        encoded = json.dumps([domain, *values], separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")
        return prefix + base64.urlsafe_b64encode(
            hashlib.sha256(prefix.encode("ascii") + encoded).digest()[:16]
        ).rstrip(b"=").decode("ascii")

    identity = (request.issuer, request.subject)
    user_id = record_id("usr_", "cuevion.canonical-user.v1", identity)
    return (
        user_id,
        record_id("vem_", "cuevion.canonical-verified-email.v1",
                  (user_id, request.email)),
        record_id("aid_", "cuevion.canonical-authentication-identity.v1", identity),
    )


def derive_invitee_record_ids(request: InviteePreparationRequest) -> tuple[str, str, str]:
    """Return canonical user/email/identity IDs without invitation authority."""
    _validate_request(request)
    return _ids(request)


def derive_invitee_provenance(request: InviteePreparationRequest) -> str:
    """Commit to the exact Team/OIDC verification and provisioning provenance.

    This value belongs to the verified-email source, independently of canonical
    record IDs. Recovery must freshly prove Team acceptance before comparing it
    with the immutable stored source; deriving a value alone grants no access.
    """
    _validate_request(request)
    encoded = json.dumps(
        ["cuevion.team-invitee.provenance.v1", request.issuer, request.subject,
         request.email, request.workspace_id, request.invitation_id,
         request.token_digest, request.inviter_user_id],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return _SOURCE + ":v1:" + hashlib.sha256(encoded).hexdigest()


def _validate_request(request: InviteePreparationRequest) -> None:
    if (
        type(request) is not InviteePreparationRequest
        or any(type(getattr(request, name)) is not str for name in request.__slots__)
        or re.fullmatch(r"[a-f0-9]{64}", request.token_digest) is None
        or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request.invitation_id) is None
    ):
        _fail(ProvisioningConflict)
    try:
        user_id, email_id, identity_id = _ids(request)
        models.VerifiedEmail(1, email_id, user_id, request.email,
            models.VerifiedEmailStatus.VERIFIED, _SOURCE, 0, 0, None, 1)
        models.AuthenticationIdentity(1, identity_id, user_id, request.issuer,
            request.subject, models.AuthenticationMethod.OIDC,
            models.AuthenticationIdentityStatus.ACTIVE, email_id, 0, None, 1)
        models.WorkspaceMembership(1, request.workspace_id, request.inviter_user_id,
            models.WorkspaceRole.OWNER, models.WorkspaceMembershipStatus.ACTIVE,
            0, 0, 1)
    except Exception:
        _fail(ProvisioningConflict)


def _prepared_request(prepared: PreparedTeamInvitee) -> InviteePreparationRequest:
    return InviteePreparationRequest(
        prepared.issuer, prepared.subject, prepared.email, prepared.workspace_id,
        prepared.invitation_id, prepared.token_digest, prepared.inviter_user_id,
    )


def _as_time(value: int) -> datetime:
    return _EPOCH + timedelta(seconds=value)


def _seconds(value: object) -> int:
    if (
        type(value) is not datetime or value.tzinfo is None
        or value.utcoffset() is None or value.microsecond != 0
    ):
        _fail(ProvisioningConflict)
    delta = value - _EPOCH
    seconds = delta.days * 86400 + delta.seconds
    if not _timestamp(seconds):
        _fail(ProvisioningConflict)
    return seconds


def _expected_rows(request: InviteePreparationRequest, created: int) -> dict[str, tuple]:
    user_id, email_id, identity_id = _ids(request)
    at = _as_time(created)
    return {
        "users": (1, user_id, "active", email_id, _DISPLAY_NAME, 1, at, at, 1),
        "verified_emails": (1, email_id, user_id, request.email, "verified",
                            derive_invitee_provenance(request), at, at, None, 1),
        "authentication_identities": (1, identity_id, user_id, request.issuer,
                            request.subject, "oidc", "active", email_id, at, None, 1),
        "workspace_memberships": (1, request.workspace_id, user_id, "member",
                                    "suspended", at, at, 1),
    }


def _same_row(actual: object, expected: tuple) -> bool:
    return (
        type(actual) is tuple and len(actual) == len(expected)
        and all(type(left) is type(right) and left == right
                for left, right in zip(actual, expected))
    )


def _all(cursor: object) -> list:
    rows = cursor.fetchall()
    if type(rows) is not list:
        _fail(ProvisioningUnavailable)
    return rows


def _validate_owner(cursor: object, request: InviteePreparationRequest) -> None:
    cursor.execute(_OWNER_SQL, (request.workspace_id, request.inviter_user_id))
    rows = _all(cursor)
    if len(rows) != 1 or type(rows[0]) is not tuple or len(rows[0]) != 15:
        _fail(ProvisioningConflict)
    row = rows[0]
    versions = (row[3], row[7], row[8], row[14])
    if any(type(v) is not int or v < 1 for v in versions) or not _same_row(
        row, (1, request.workspace_id, "active", row[3], 1, request.inviter_user_id,
              "active", row[7], row[8], 1, request.workspace_id,
              request.inviter_user_id, "owner", "active", row[14]),
    ):
        _fail(ProvisioningConflict)


def _lock_keys(request: InviteePreparationRequest) -> tuple[int, ...]:
    values = (
        ["identity", request.issuer, request.subject],
        ["email", request.email],
        ["invitation", request.workspace_id, request.invitation_id],
    )
    return tuple(sorted({int.from_bytes(hashlib.sha256(
        json.dumps(["cuevion.team-invitee.lock.v1", *value],
                   separators=(",", ":")).encode("ascii")
    ).digest()[:8], "big", signed=True) for value in values}))


def _read_graph(cursor: object, request: InviteePreparationRequest) -> dict[str, list]:
    user_id, email_id, identity_id = _ids(request)
    parameters = {
        "users": (user_id,),
        "verified_emails": (request.email, email_id, user_id),
        "authentication_identities": (request.issuer, request.subject, identity_id, user_id),
        "workspace_memberships": (user_id,),
    }
    graph = {}
    for table, columns in _COLUMNS.items():
        cursor.execute(
            f"SELECT {', '.join(columns)} FROM cuevion_account.{table} "
            f"WHERE {_SELECTS[table]} LIMIT 2 FOR UPDATE", parameters[table],
        )
        graph[table] = _all(cursor)
    return graph


def _validate_graph(
    graph: dict[str, list], request: InviteePreparationRequest, now: int,
) -> PreparedTeamInvitee:
    if any(len(rows) != 1 for rows in graph.values()):
        _fail(ProvisioningConflict)
    user = graph["users"][0]
    if type(user) is not tuple or len(user) != 9:
        _fail(ProvisioningConflict)
    created = _seconds(user[6])
    if created > now:
        _fail(ProvisioningConflict)
    expected = _expected_rows(request, created)
    member = graph["workspace_memberships"][0]
    if type(member) is not tuple or len(member) != 8:
        _fail(ProvisioningConflict)
    if type(member[4]) is str and member[4] == "active":
        updated = _seconds(member[6])
        if updated < created or updated > now:
            _fail(ProvisioningConflict)
        row = list(expected["workspace_memberships"])
        row[4], row[6], row[7] = "active", _as_time(updated), 2
        expected["workspace_memberships"] = tuple(row)
    if any(not _same_row(graph[table][0], row) for table, row in expected.items()):
        _fail(ProvisioningConflict)
    user_id, email_id, identity_id = _ids(request)
    return PreparedTeamInvitee(user_id, email_id, identity_id, request.issuer,
        request.subject, request.email, request.workspace_id, request.invitation_id,
        request.token_digest, request.inviter_user_id, created, member[7], member[4], 1)


class PostgreSQLTeamInviteeRepository:
    """Small synchronous writer with bounded retries and exact commit recovery."""

    def __init__(self, connection_factory: Callable[[], object]) -> None:
        if not callable(connection_factory):
            _fail(ProvisioningUnavailable)
        self._connection_factory = connection_factory

    def prepare(self, request: InviteePreparationRequest, now: int) -> PreparedTeamInvitee:
        _validate_request(request)
        return self._run(request, now, None)

    def finalize(self, prepared: PreparedTeamInvitee, now: int) -> PreparedTeamInvitee:
        """Activate only after the caller freshly proves exact Team acceptance."""
        if type(prepared) is not PreparedTeamInvitee:
            _fail(ProvisioningConflict)
        prepared.__post_init__()
        return self._run(_prepared_request(prepared), now, prepared)

    def _transaction(
        self, request: InviteePreparationRequest, now: int,
        prepared: PreparedTeamInvitee | None, *, reconcile: bool = False,
    ) -> PreparedTeamInvitee:
        connection = None
        cursor = None
        committed = False
        try:
            connection = self._connection_factory()
            if (
                connection.autocommit is not False
                or isinstance(connection.info.transaction_status, bool)
                or connection.info.transaction_status != 0
            ):
                _fail(ProvisioningUnavailable)
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            cursor.execute("SET LOCAL statement_timeout = '5000ms'")
            cursor.execute("SET LOCAL lock_timeout = '2000ms'")
            for key in _lock_keys(request):
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            _validate_owner(cursor, request)
            graph = _read_graph(cursor, request)
            if all(not rows for rows in graph.values()):
                if prepared is not None or reconcile:
                    _fail(ProvisioningUnavailable if reconcile else ProvisioningConflict)
                expected = _expected_rows(request, now)
                for table, values in expected.items():
                    columns = _COLUMNS[table]
                    cursor.execute(
                        f"INSERT INTO cuevion_account.{table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join('%s' for _ in columns)})", values,
                    )
                graph = _read_graph(cursor, request)
            result = _validate_graph(graph, request, now)
            if prepared is not None:
                if (result.created_at != prepared.created_at
                        or result.membership_row_version < prepared.membership_row_version):
                    _fail(ProvisioningConflict)
                if result.status == "suspended":
                    if reconcile:
                        _fail(ProvisioningUnavailable)
                    at = _as_time(result.created_at)
                    cursor.execute(_FINALIZE_SQL,
                        (_as_time(now), request.workspace_id, result.user_id, at, at))
                    if _all(cursor) != [(2,)]:
                        _fail(ProvisioningConflict)
                    result = _validate_graph(_read_graph(cursor, request), request, now)
                    if result.status != "active":
                        _fail(ProvisioningConflict)
            if reconcile:
                connection.rollback()
            else:
                connection.commit()
                committed = True
            return result
        finally:
            # Always attempt every cleanup action. Errors retain no SQL/claim data
            # at the public boundary; a known result still requires clean closure.
            cleanup_failed = False
            for target, method in ((cursor, "close"), (connection, "rollback"),
                                   (connection, "close")):
                if target is None or (method == "rollback" and committed):
                    continue
                try:
                    getattr(target, method)()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                _fail(ProvisioningUnavailable)

    def _run(
        self, request: InviteePreparationRequest, now: int,
        prepared: PreparedTeamInvitee | None,
    ) -> PreparedTeamInvitee:
        if not _timestamp(now):
            _fail(ProvisioningConflict)
        conflict = False
        for attempt in range(2):
            try:
                return self._transaction(request, now, prepared)
            except ProvisioningConflict:
                conflict = True
                break
            except Exception as error:
                sqlstate = getattr(error, "sqlstate", None)
                if sqlstate in ("40001", "40P01") and attempt == 0:
                    continue
                if sqlstate in ("23505", "23503", "23514"):
                    # Uniqueness is the cross-writer boundary. A concurrent
                    # exact attempt may nevertheless have committed first.
                    conflict = True
                break
        if not conflict:
            try:
                return self._transaction(request, now, prepared, reconcile=True)
            except ProvisioningConflict:
                conflict = True
            except Exception:
                pass
        else:
            # Only exact rows from this deterministic intent can recover a race.
            try:
                return self._transaction(request, now, prepared, reconcile=True)
            except Exception:
                pass
        _fail(ProvisioningConflict if conflict else ProvisioningUnavailable)
