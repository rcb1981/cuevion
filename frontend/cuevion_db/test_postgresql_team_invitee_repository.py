"""Offline transaction/authority tests; no PostgreSQL, DDL, or network access."""

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from types import SimpleNamespace

from cuevion_db import postgresql_team_invitee_repository as module


WORKSPACE = "wsp_" + "A" * 22
OWNER = "usr_" + "B" * 21 + "A"
NOW = 1_800_000_000
REQUEST = module.InviteePreparationRequest(
    "https://issuer.example/", "oidc|invitee", "invitee@example.com", WORKSPACE,
    "inv_test123", "a" * 64, OWNER,
)


class DriverError(Exception):
    def __init__(self, sqlstate=None):
        super().__init__("unsafe driver detail: invitee@example.com")
        self.sqlstate = sqlstate


class Database:
    """Models atomic transactions and the inspected base-table uniqueness rules.

    An RLock serializes the offline simulator. PostgreSQL lock/SSI behavior is
    asserted through emitted SQL and injected serialization/constraint failures,
    not claimed as an integration test against a running PostgreSQL server.
    """

    def __init__(self):
        self.rows = {table: [] for table in module._COLUMNS}
        self.owner = (1, WORKSPACE, "active", 1, 1, OWNER, "active", 1, 1,
                      1, WORKSPACE, OWNER, "owner", "active", 1)
        self.lock = threading.RLock()
        self.statements = []
        self.connections = []
        self.fail_insert_number = None
        self.fail_commit = None
        self.fail_first_sqlstate = None
        self.corrupt_read = None

    def connect(self):
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class Connection:
    autocommit = False

    def __init__(self, database):
        self.database = database
        self.info = SimpleNamespace(transaction_status=0)
        self.transaction = None
        self.locked = False
        self.closed = False
        self.cursor_closed = False
        self.insert_count = 0

    def cursor(self):
        return Cursor(self)

    def begin(self):
        self.database.lock.acquire()
        self.locked = True
        self.transaction = copy.deepcopy(self.database.rows)

    def commit(self):
        failure = self.database.fail_commit
        self.database.fail_commit = None
        if failure == "before":
            raise DriverError()
        self.database.rows = self.transaction
        self.transaction = None
        self.release()
        if failure == "after":
            raise DriverError()

    def release(self):
        if self.locked:
            self.locked = False
            self.database.lock.release()

    def rollback(self):
        self.transaction = None
        self.release()

    def close(self):
        self.closed = True
        self.release()


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []

    def close(self):
        self.connection.cursor_closed = True

    def fetchall(self):
        return self.result

    def execute(self, sql, parameters=()):
        c = self.connection
        db = c.database
        sql = " ".join(sql.split())
        db.statements.append((sql, parameters))
        self.result = []
        if sql.startswith("SET TRANSACTION"):
            c.begin()
            if db.fail_first_sqlstate:
                state = db.fail_first_sqlstate
                db.fail_first_sqlstate = None
                raise DriverError(state)
            return
        if sql.startswith("SET LOCAL") or "pg_advisory_xact_lock" in sql:
            return
        if "FROM cuevion_account.workspaces w" in sql:
            self.result = [] if db.owner is None else [db.owner]
            return
        if sql.startswith("SELECT"):
            table = sql.split("FROM cuevion_account.")[1].split()[0]
            rows = c.transaction[table]
            if table == "users":
                self.result = [r for r in rows if r[1] == parameters[0]]
            elif table == "verified_emails":
                email, email_id, user_id = parameters
                self.result = [r for r in rows if
                    (r[3] == email and r[4] == "verified" and r[8] is None)
                    or r[1] == email_id or r[2] == user_id]
            elif table == "authentication_identities":
                issuer, subject, identity_id, user_id = parameters
                self.result = [r for r in rows if (r[3], r[4]) == (issuer, subject)
                    or r[1] == identity_id or r[2] == user_id]
            elif table == "workspace_memberships":
                self.result = [r for r in rows if r[2] == parameters[0]]
            if db.corrupt_read:
                self.result = db.corrupt_read(table, self.result)
            return
        if sql.startswith("INSERT"):
            c.insert_count += 1
            if db.fail_insert_number == c.insert_count:
                db.fail_insert_number = None
                raise DriverError()
            table = sql.split("INTO cuevion_account.")[1].split()[0]
            rows = c.transaction[table]
            value = tuple(parameters)
            if table == "verified_emails":
                if any(r[1] == value[1] or (
                    r[3] == value[3] and r[4] == value[4] == "verified"
                    and r[8] is value[8] is None) for r in rows):
                    raise DriverError("23505")
            elif table == "authentication_identities":
                if any(r[1] == value[1] or r[3:5] == value[3:5] for r in rows):
                    raise DriverError("23505")
            elif table == "users":
                if any(r[1] == value[1] for r in rows):
                    raise DriverError("23505")
            elif table == "workspace_memberships":
                if any(r[1:3] == value[1:3] for r in rows):
                    raise DriverError("23505")
            rows.append(value)
            return
        if sql.startswith("UPDATE cuevion_account.workspace_memberships"):
            now, workspace, user, created, updated = parameters
            for i, row in enumerate(c.transaction["workspace_memberships"]):
                if (row[0:6] == (1, workspace, user, "member", "suspended", created)
                        and row[6:] == (updated, 1)):
                    changed = list(row)
                    changed[4], changed[6], changed[7] = "active", now, 2
                    c.transaction["workspace_memberships"][i] = tuple(changed)
                    self.result = [(2,)]
            return
        raise AssertionError("Unexpected SQL operation")


class PostgreSQLTeamInviteeRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.repository = module.PostgreSQLTeamInviteeRepository(self.db.connect)

    def prepare(self):
        return self.repository.prepare(REQUEST, NOW)

    def test_prepare_creates_exact_member_graph_without_owner_operation(self):
        result = self.prepare()
        self.assertEqual((result.status, result.membership_row_version), ("suspended", 1))
        self.assertEqual({table: len(rows) for table, rows in self.db.rows.items()},
                         {table: 1 for table in module._COLUMNS})
        self.assertEqual(self.db.rows["users"][0][2], "active")
        self.assertEqual(self.db.rows["authentication_identities"][0][5], "oidc")
        source = self.db.rows["verified_emails"][0][5]
        self.assertEqual(source, module.derive_invitee_provenance(REQUEST))
        self.assertRegex(source, r"^team-invite-oidc:v1:[a-f0-9]{64}$")
        self.assertLessEqual(len(source), 128)
        self.assertEqual(self.db.rows["workspace_memberships"][0][3:5],
                         ("member", "suspended"))
        sql = " ".join(statement for statement, _ in self.db.statements)
        for forbidden in ("initial_account_operations", "security_events", "DELETE", "CREATE"):
            self.assertNotIn(forbidden, sql)
        self.assertNotIn(REQUEST.email, sql)
        self.assertNotIn(REQUEST.subject, sql)
        self.assertNotIn(REQUEST.token_digest, repr(self.db.rows))
        self.assertTrue(all(c.closed and c.cursor_closed for c in self.db.connections))

    def test_prepare_retry_uses_original_timestamp_and_no_additional_inserts(self):
        result = self.prepare()
        self.assertEqual(module.derive_invitee_record_ids(REQUEST),
                         (result.user_id, result.email_id, result.identity_id))
        self.assertEqual(module.derive_invitee_record_ids(
            replace(REQUEST, token_digest="b" * 64)),
            module.derive_invitee_record_ids(REQUEST))
        self.assertNotEqual(module.derive_invitee_provenance(
            replace(REQUEST, token_digest="b" * 64)),
            module.derive_invitee_provenance(REQUEST))
        with self.assertRaises(module.ProvisioningConflict):
            module.derive_invitee_record_ids(object())
        rows = copy.deepcopy(self.db.rows)
        replay = self.repository.prepare(REQUEST, NOW + 60)
        self.assertEqual(replay, result)
        self.assertEqual(self.db.rows, rows)
        inserts = [s for s, _ in self.db.statements if s.startswith("INSERT")]
        self.assertEqual(len(inserts), 4)

    def test_user_and_identity_ids_exclude_every_email_and_invitation_field(self):
        user_id, email_id, identity_id = module.derive_invitee_record_ids(REQUEST)
        cases = (
            ("email", "different@example.com"),
            ("workspace_id", "wsp_" + "C" * 21 + "A"),
            ("inviter_user_id", "usr_" + "D" * 21 + "A"),
            ("invitation_id", "another_invitation"),
            ("token_digest", "b" * 64),
        )
        for field, value in cases:
            with self.subTest(field=field):
                request = replace(REQUEST, **{field: value})
                other_user, other_email, other_identity = module.derive_invitee_record_ids(request)
                self.assertEqual(other_user, user_id)
                self.assertEqual(other_identity, identity_id)
                if field == "email":
                    self.assertNotEqual(other_email, email_id)
                else:
                    self.assertEqual(other_email, email_id)
                self.assertNotEqual(module.derive_invitee_provenance(request),
                                    module.derive_invitee_provenance(REQUEST))

    def test_issuer_and_subject_independently_change_canonical_identity_ids(self):
        original = module.derive_invitee_record_ids(REQUEST)
        for field, value in (("issuer", "https://another-issuer.example/"),
                             ("subject", "oidc|another-person")):
            with self.subTest(field=field):
                request = replace(REQUEST, **{field: value})
                changed = module.derive_invitee_record_ids(request)
                self.assertNotEqual(changed[0], original[0])
                self.assertNotEqual(changed[2], original[2])
                # The email-record ID is also bound to its canonical user.
                self.assertNotEqual(changed[1], original[1])
                self.assertNotEqual(module.derive_invitee_provenance(request),
                                    module.derive_invitee_provenance(REQUEST))

    def test_identifiers_use_distinct_domains_and_canonical_encoding(self):
        ids = module.derive_invitee_record_ids(REQUEST)
        for prefix, identifier in zip(("usr_", "vem_", "aid_"), ids):
            self.assertRegex(identifier, "^" + prefix + r"[A-Za-z0-9_-]{21}[AQgw]$")
        self.assertEqual(len({identifier[4:] for identifier in ids}), 3)

    def test_normalized_email_variants_share_ids_provenance_and_prepared_rows(self):
        prepared = self.prepare()
        for email in ("  Invitee@EXAMPLE.com  ", "\tINVITEE@example.com\r\n"):
            with self.subTest(email=email):
                request = replace(REQUEST, email=email)
                self.assertEqual(request.email, "invitee@example.com")
                self.assertEqual(module.derive_invitee_record_ids(request),
                                 module.derive_invitee_record_ids(REQUEST))
                self.assertEqual(module.derive_invitee_provenance(request),
                                 module.derive_invitee_provenance(REQUEST))
                self.assertEqual(self.repository.prepare(request, NOW + 5), prepared)
        self.assertEqual(sum(len(rows) for rows in self.db.rows.values()), 4)
        self.assertEqual(self.db.rows["verified_emails"][0][3], "invitee@example.com")

    def test_dot_and_plus_email_variants_remain_distinct_email_authority(self):
        original = module.derive_invitee_record_ids(REQUEST)
        for email in ("in.vitee@example.com", "invitee+alias@example.com"):
            with self.subTest(email=email):
                request = replace(REQUEST, email=email)
                changed = module.derive_invitee_record_ids(request)
                self.assertEqual(changed[0], original[0])
                self.assertEqual(changed[2], original[2])
                self.assertNotEqual(changed[1], original[1])
                self.assertEqual(request.email, email)

    def test_creation_timestamp_is_independent_of_ids_and_provenance(self):
        first = self.prepare()
        other_database = Database()
        later = module.PostgreSQLTeamInviteeRepository(other_database.connect).prepare(
            REQUEST, NOW + 500)
        self.assertNotEqual(first.created_at, later.created_at)
        self.assertEqual((first.user_id, first.email_id, first.identity_id),
                         (later.user_id, later.email_id, later.identity_id))
        self.assertEqual(self.db.rows["verified_emails"][0][5],
                         other_database.rows["verified_emails"][0][5])

    def test_finalize_cas_and_replays_preserve_creation_and_membership_version(self):
        prepared = self.prepare()
        source = self.db.rows["verified_emails"][0][5]
        result = self.repository.finalize(prepared, NOW + 5)
        self.assertEqual((result.status, result.membership_row_version), ("active", 2))
        replay = self.repository.finalize(prepared, NOW + 100)
        self.assertEqual(result, replay)
        self.assertEqual(self.repository.prepare(REQUEST, NOW + 100), result)
        self.assertEqual(self.db.rows["workspace_memberships"][0][6],
                         datetime.fromtimestamp(NOW + 5, timezone.utc))
        self.assertEqual(len([s for s, _ in self.db.statements if s.startswith("UPDATE")]), 1)
        self.assertEqual(self.db.rows["verified_emails"][0][5], source)

    def test_first_write_failure_rolls_back_every_row(self):
        self.db.fail_insert_number = 3
        with self.assertRaises(module.ProvisioningUnavailable):
            self.prepare()
        self.assertTrue(all(not rows for rows in self.db.rows.values()))
        self.assertEqual(self.prepare().status, "suspended")

    def test_ambiguous_prepare_commit_recovers_only_committed_exact_graph(self):
        self.db.fail_commit = "after"
        result = self.prepare()
        self.assertEqual(result.status, "suspended")
        self.assertEqual(len(self.db.connections), 2)
        self.assertEqual(sum(len(rows) for rows in self.db.rows.values()), 4)

    def test_commit_failure_before_commit_does_not_create_on_reconciliation(self):
        self.db.fail_commit = "before"
        with self.assertRaises(module.ProvisioningUnavailable):
            self.prepare()
        self.assertTrue(all(not rows for rows in self.db.rows.values()))
        self.assertEqual(len([s for s, _ in self.db.statements if s.startswith("INSERT")]), 4)

    def test_ambiguous_finalize_commit_recovers_active_graph(self):
        prepared = self.prepare()
        self.db.fail_commit = "after"
        result = self.repository.finalize(prepared, NOW + 1)
        self.assertEqual(result.status, "active")
        self.assertEqual(result.membership_row_version, 2)

    def test_failed_finalize_commit_preserves_suspended_graph(self):
        prepared = self.prepare()
        self.db.fail_commit = "before"
        with self.assertRaises(module.ProvisioningUnavailable):
            self.repository.finalize(prepared, NOW + 1)
        self.assertEqual(self.db.rows["workspace_memberships"][0][4], "suspended")
        self.assertEqual(self.repository.finalize(prepared, NOW + 2).status, "active")

    def test_serialization_failure_gets_one_fresh_transaction_retry(self):
        self.db.fail_first_sqlstate = "40001"
        self.assertEqual(self.prepare().status, "suspended")
        self.assertEqual(len(self.db.connections), 2)

    def test_sorted_locks_and_serializable_queries_are_emitted(self):
        self.prepare()
        self.assertEqual(self.db.statements[0][0],
                         "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        keys = [p[0] for sql, p in self.db.statements if "pg_advisory_xact_lock" in sql]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual(len(keys), 3)
        self.assertIn("FOR SHARE OF w, u, m", self.db.statements[6][0])

    def test_same_invitation_concurrent_attempts_reuse_one_graph(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.prepare(), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(sum(len(rows) for rows in self.db.rows.values()), 4)

    def test_changed_context_keeps_identity_ids_but_rejects_authority_rebinding(self):
        prepared = self.prepare()
        rows = copy.deepcopy(self.db.rows)
        original_owner = self.db.owner
        cases = (
            replace(REQUEST, invitation_id="another_invitation"),
            replace(REQUEST, email="different@example.com"),
            replace(REQUEST, token_digest="b" * 64),
            replace(REQUEST, workspace_id="wsp_" + "C" * 21 + "A"),
            replace(REQUEST, inviter_user_id="usr_" + "D" * 21 + "A"),
        )
        for request in cases:
            with self.subTest(request=request):
                owner = list(original_owner)
                owner[1] = owner[10] = request.workspace_id
                owner[5] = owner[11] = request.inviter_user_id
                self.db.owner = tuple(owner)
                user_id, _, identity_id = module.derive_invitee_record_ids(request)
                self.assertEqual(user_id, prepared.user_id)
                self.assertEqual(identity_id, prepared.identity_id)
                before = len(self.db.statements)
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.prepare(request, NOW + 1)
                # Each alternate owner/workspace is valid. Rejection must reach
                # the persisted graph/provenance, not stop at owner validation.
                self.assertTrue(any("FROM cuevion_account.verified_emails " in sql
                    for sql, _ in self.db.statements[before:]))
                self.assertEqual(self.db.rows, rows)
        self.db.owner = original_owner
        self.assertEqual(self.prepare(), prepared)
        self.assertEqual(sum(len(rows) for rows in self.db.rows.values()), 4)
        self.assertEqual(len(self.db.rows["users"]), 1)

    def test_finalized_graph_does_not_adopt_replacement_invitation_provenance(self):
        prepared = self.repository.finalize(self.prepare(), NOW + 1)
        rows = copy.deepcopy(self.db.rows)
        original_owner = self.db.owner
        for changes in ({"invitation_id": "replacement"}, {"token_digest": "b" * 64},
                        {"inviter_user_id": "usr_" + "D" * 21 + "A"},
                        {"workspace_id": "wsp_" + "C" * 21 + "A"}):
            with self.subTest(changes=changes):
                request = replace(REQUEST, **changes)
                owner = list(original_owner)
                owner[1] = owner[10] = request.workspace_id
                owner[5] = owner[11] = request.inviter_user_id
                self.db.owner = tuple(owner)
                self.assertEqual(module.derive_invitee_record_ids(request),
                                 module.derive_invitee_record_ids(REQUEST))
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.prepare(request, NOW + 2)
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.finalize(replace(prepared, **changes), NOW + 2)
                self.assertEqual(self.db.rows, rows)
        self.db.owner = original_owner

    def test_new_identity_with_existing_email_never_links_to_existing_user(self):
        prepared = self.prepare()
        rows = copy.deepcopy(self.db.rows)
        for changes in ({"subject": "oidc|another-person"},
                        {"issuer": "https://another-issuer.example/"}):
            with self.subTest(changes=changes):
                request = replace(REQUEST, **changes)
                self.assertNotEqual(module.derive_invitee_record_ids(request)[0],
                                    prepared.user_id)
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.prepare(request, NOW + 1)
                self.assertEqual(self.db.rows, rows)

    def test_legacy_malformed_or_changed_provenance_cannot_prepare_or_finalize(self):
        sources = ("team-invite-oidc", "team-invite-oidc:v1:bad",
                   "team-invite-oidc:v2:" + "a" * 64,
                   "team-invite-oidc:v1:" + "0" * 64, "other-source")
        for source in sources:
            with self.subTest(source=source):
                self.setUp()
                prepared = self.prepare()
                row = list(self.db.rows["verified_emails"][0])
                row[5] = source
                self.db.rows["verified_emails"][0] = tuple(row)
                rows = copy.deepcopy(self.db.rows)
                with self.assertRaises(module.ProvisioningConflict):
                    self.prepare()
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.finalize(prepared, NOW + 1)
                self.assertEqual(self.db.rows, rows)

    def test_concurrent_same_email_different_identity_has_one_winner(self):
        requests = [REQUEST, replace(REQUEST, subject="oidc|other")]
        def attempt(request):
            try:
                return self.repository.prepare(request, NOW).status
            except module.ProvisioningConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, requests))
        self.assertCountEqual(outcomes, ["suspended", "conflict"])
        self.assertEqual(sum(len(rows) for rows in self.db.rows.values()), 4)

    def test_wrong_existing_identity_id_fails_closed(self):
        self.prepare()
        row = list(self.db.rows["authentication_identities"][0])
        row[1] = "aid_" + "Z" * 21 + "A"
        self.db.rows["authentication_identities"][0] = tuple(row)
        with self.assertRaises(module.ProvisioningConflict):
            self.prepare()

    def test_different_workspace_membership_and_extra_emails_fail_closed(self):
        for table in ("workspace_memberships", "verified_emails"):
            with self.subTest(table=table):
                self.setUp()
                self.prepare()
                row = list(self.db.rows[table][0])
                row[1] = ("wsp_" if table == "workspace_memberships" else "vem_") + "C" * 21 + "A"
                self.db.rows[table].append(tuple(row))
                with self.assertRaises(module.ProvisioningConflict):
                    self.prepare()

    def test_no_reactivation_after_revocation_or_later_membership_version(self):
        for status, version in (("removed", 3), ("suspended", 3), ("active", 4), ("suspended", 2)):
            with self.subTest(status=status, version=version):
                self.setUp()
                prepared = self.prepare()
                row = list(self.db.rows["workspace_memberships"][0])
                row[4], row[7] = status, version
                self.db.rows["workspace_memberships"][0] = tuple(row)
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.finalize(prepared, NOW + 1)
                self.assertEqual(self.db.rows["workspace_memberships"][0][4:5], (status,))

    def test_finalize_rechecks_owner_and_workspace_current_authority(self):
        for position, value in ((2, "suspended"), (6, "disabled"), (12, "member"), (13, "removed")):
            with self.subTest(position=position):
                self.setUp()
                prepared = self.prepare()
                owner = list(self.db.owner)
                owner[position] = value
                self.db.owner = tuple(owner)
                with self.assertRaises(module.ProvisioningConflict):
                    self.repository.finalize(prepared, NOW + 1)
                self.assertEqual(self.db.rows["workspace_memberships"][0][4], "suspended")

    def test_partial_graph_or_changed_security_epoch_rejected(self):
        self.prepare()
        user = list(self.db.rows["users"][0])
        user[5] = 2
        self.db.rows["users"][0] = tuple(user)
        with self.assertRaises(module.ProvisioningConflict):
            self.prepare()
        self.setUp()
        self.prepare()
        self.db.rows["authentication_identities"] = []
        with self.assertRaises(module.ProvisioningConflict):
            self.prepare()

    def test_database_bool_versions_and_future_timestamps_rejected(self):
        self.prepare()
        user = list(self.db.rows["users"][0])
        user[8] = True
        self.db.rows["users"][0] = tuple(user)
        with self.assertRaises(module.ProvisioningConflict):
            self.prepare()
        self.setUp()
        self.prepare()
        with self.assertRaises(module.ProvisioningConflict):
            self.repository.prepare(REQUEST, NOW - 1)

    def test_requests_are_validated_and_repr_does_not_expose_claims(self):
        prepared = self.prepare()
        for value in (REQUEST, prepared):
            self.assertNotIn(REQUEST.email, repr(value))
            self.assertNotIn(REQUEST.token_digest, repr(value))
            with self.assertRaises(FrozenInstanceError):
                value.email = "other@example.com"
        for kwargs in ({"email": "not-an-email"},
                       {"issuer": "line\nbreak"}, {"token_digest": "raw-secret-token"},
                       {"invitation_id": "bad/identifier"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(module.ProvisioningConflict):
                replace(REQUEST, **kwargs)
        with self.assertRaises(module.ProvisioningConflict):
            replace(prepared, email="  INVITEE@example.com  ")

    def test_failed_driver_details_are_not_retained_by_public_error(self):
        def unavailable():
            raise DriverError()
        repository = module.PostgreSQLTeamInviteeRepository(unavailable)
        with self.assertRaises(module.ProvisioningUnavailable) as caught:
            repository.prepare(REQUEST, NOW)
        self.assertNotIn(REQUEST.email, repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
