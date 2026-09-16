"""Real disposable PostgreSQL proof: schema, immutable rows, races, rollback.

Set CUEVION_TEST_POSTGRES_BIN to a local PostgreSQL bin directory. Never reads
an application DSN; creates its own cluster and connects only to its Unix socket.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psycopg

from api.auth import account_authority, auth0_flow, models, runtime, session_store
from cuevion_auth import current_account_repository_contract as contract
from cuevion_migration.owner_passkey import MigrationDenied, FIELDS
from cuevion_db import identity_inventory_diagnostic as inventory
from cuevion_db import postgresql_current_account_repository as canonical
from cuevion_db import postgresql_owner_passkey_migration as repository
from tests.cuevion_migration import test_owner_passkey_migration as fixtures
from tests.cuevion_db import test_postgresql_current_account_repository as rows


class PostgreSQLOwnerMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("CUEVION_TEST_POSTGRES_BIN")
        initdb = str(Path(directory) / "initdb") if directory else shutil.which("initdb")
        if not initdb or not Path(initdb).is_file():
            raise unittest.SkipTest("local PostgreSQL binaries required for disposable integration proof")
        cls.pg_ctl = str(Path(initdb).parent / "pg_ctl")
        cls.temp = tempfile.TemporaryDirectory(prefix="cuevion-owner-pg-", dir="/tmp")
        cls.cluster = cls.temp.name + "/data"
        try:
            subprocess.run([initdb, "-D", cls.cluster, "-A", "trust", "-U", "postgres", "--encoding=UTF8", "--no-locale"],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([cls.pg_ctl, "-D", cls.cluster, "-l", cls.temp.name + "/postgres.log",
                            "-o", f"-k {cls.temp.name} -h '' -p 55439", "-w", "start"],
                           check=True, capture_output=True, timeout=30)
        except Exception:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        subprocess.run([cls.pg_ctl, "-D", cls.cluster, "-m", "fast", "-w", "stop"],
                       check=True, capture_output=True, timeout=30)
        cls.temp.cleanup()

    @classmethod
    def connect(cls):
        return psycopg.connect(host=cls.temp.name, port=55439, dbname="postgres", user="postgres",
                               connect_timeout=5, options="-c timezone=UTC")

    def setUp(self):
        # This connection is hard-bound to the disposable cluster above.
        migration = importlib.import_module("migrations.versions.0001_account_schema_1")
        self.raw = {
            "users": [rows._user_segment()],
            "verified_emails": [rows._email_segment(verification_source="cuevion_first_account_operator_v1")],
            "authentication_identities": [rows._identity_segment(issuer=auth0_flow.AUTH0_ISSUER,
                subject=fixtures.SUBJECT, method="email_otp")],
            "workspaces": [rows._workspace_segment()],
            "workspace_memberships": [rows._membership_segment(role="owner")],
        }
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP SCHEMA IF EXISTS cuevion_account CASCADE")
                with patch.object(migration, "op", SimpleNamespace(execute=cursor.execute)):
                    migration.upgrade()
                for table, columns, _, _ in inventory._TABLES:
                    for row in self.raw[table]:
                        cursor.execute(f"INSERT INTO cuevion_account.{table} ({', '.join(columns)}) "
                            f"VALUES ({', '.join(['%s'] * len(columns))})", row)
        memory = fixtures.Memory()
        self.session, _ = session_store.create_server_session(session_store.AuthSessionStore(memory),
            secret=fixtures.tokens.SESSION_SECRET, user_id=rows.USER_ID, workspace_id=rows.WORKSPACE_ID,
            security_epoch=3, issuer=auth0_flow.AUTH0_ISSUER, subject=fixtures.SUBJECT, now=fixtures.NOW - 100)
        self.reader = inventory.PostgreSQLIdentityInventoryDiagnostic(self.connect)
        self.expected = self.reader.read(self.session)
        self.target = auth0_flow.ValidatedIdentityEvidence(auth0_flow.AUTH0_ISSUER, fixtures.NEW_SUBJECT,
                                                         rows.EMAIL, fixtures.NOW - 10, fixtures.NOW + 300)
        self.repository = repository.PostgreSQLOwnerPasskeyMigration(self.connect)

    def attach(self, **overrides):
        args = dict(now=fixtures.NOW, revalidate=lambda: None)
        args.update(overrides)
        return self.repository.attach(self.expected, self.session, self.target, **args)

    def unchanged(self):
        actual = self.reader.read(self.session)
        for field in FIELDS:
            self.assertEqual(getattr(actual, field), getattr(self.expected, field))

    def test_two_identities_resolve_through_normal_runtime_same_owner_and_epoch(self):
        new = self.attach()
        self.assertEqual(new.method, models.AuthenticationMethod.OIDC)
        reader = account_authority.RuntimeAccountAuthority(self.connect,
            canonical.PostgreSQLCurrentAccountRepository(self.connect))
        for subject in (fixtures.SUBJECT, fixtures.NEW_SUBJECT):
            key = contract.AuthenticationIdentityLookupKey(auth0_flow.AUTH0_ISSUER, subject)
            result = reader.resolve_current_account_by_identity(key)
            self.assertEqual(result.outcome, contract.CurrentAccountReadOutcome.FOUND)
            self.assertTrue(account_authority.auth0_authority_matches(result, key, rows.EMAIL))
            for name in ("user", "primary_verified_email", "workspace", "workspace_membership"):
                self.assertEqual(getattr(result.authority, name), getattr(self.expected.authority, name))
            runtime._require_provisioned_team_access(result.authority, key.issuer, key.subject, {},
                lambda _: self.fail("initial OWNER entered invite path"))
            session = replace(self.session, subject=subject)
            self.assertEqual(runtime._current_authority_member_context(session, result.authority).membership_role, "owner")
        with self.connect() as connection:
            for table, columns, _, order in inventory._TABLES:
                records = connection.execute(f"SELECT {', '.join(columns)} FROM cuevion_account.{table} ORDER BY {order}").fetchall()
                if table == "authentication_identities":
                    self.assertEqual(len(records), 2)
                    self.assertIn(self.raw[table][0], records)  # Every original persisted column unchanged.
                else:
                    self.assertEqual(records, self.raw[table])

    def test_retry_cannot_create_duplicate(self):
        self.attach()
        with self.assertRaises(MigrationDenied):
            self.attach()
        self.assertEqual(len(self.reader.read(self.session).identities), 2)

    def test_signed_callback_to_real_writer_status_and_existing_session_restoration(self):
        harness = fixtures.MigrationTests()
        harness.setUp()
        harness.reader = self.reader
        harness.repository = self.repository
        harness.collaboration()
        harness.start()
        original = dict(harness.memory.values)
        response = harness.callback()
        self.assertIn(("Location", "/?passkey_migration=identity_added"), response.headers)
        self.assertEqual(harness.memory.values[harness.session_key], original[harness.session_key])
        response = harness.response("status", now=fixtures.NOW + 2)
        import json
        result = json.loads(response.body)
        self.assertEqual(response.status, 200, result)
        self.assertTrue(result["newIdentityResolvesSameUser"])
        self.assertTrue(result["oldEmailIdentityActive"])
        reader = account_authority.RuntimeAccountAuthority(self.connect,
            canonical.PostgreSQLCurrentAccountRepository(self.connect))
        restored = runtime.resolve_authenticated_member_session(harness.headers,
            environment=fixtures.ENV, now=fixtures.NOW + 2,
            session_store_factory=lambda _: harness.store, authority_factory=lambda _: reader)
        self.assertIs(restored.outcome, runtime.MemberResolutionOutcome.AUTHENTICATED)
        self.assertEqual(len(self.reader.read(self.session).identities), 2)
        diagnostic = fixtures.migration.diagnostic.diagnostic_response("GET", harness.headers,
            fixtures.migration.diagnostic.ROUTE, environment=harness.environment, now=fixtures.NOW + 2,
            reader_factory=lambda _: self.reader, transport_factory=lambda _: harness.memory)
        self.assertEqual(diagnostic.status, 200, diagnostic.body)
        self.assertEqual(json.loads(diagnostic.body)["counts"]["authenticationIdentities"], 2)

    def test_concurrent_migrations_one_commits_one_fails(self):
        barrier = threading.Barrier(2)
        def competing(subject):
            barrier.wait(timeout=5)
            try:
                return self.repository.attach(self.expected, self.session, replace(self.target, subject=subject),
                                              now=fixtures.NOW, revalidate=lambda: None)
            except MigrationDenied:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(competing, (fixtures.NEW_SUBJECT, "auth0|competing-owner")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.reader.read(self.session).identities), 2)

    def test_post_write_authority_failure_rolls_back(self):
        original = repository._authority
        def fail_new(cursor, issuer, subject, workspace_id):
            if subject == fixtures.NEW_SUBJECT:
                raise MigrationDenied()
            return original(cursor, issuer, subject, workspace_id)
        with patch.object(repository, "_authority", side_effect=fail_new), self.assertRaises(MigrationDenied):
            self.attach()
        self.unchanged()

    def test_session_or_team_changes_before_commit_roll_back(self):
        for fail_at in (1, 2):
            calls = []
            def revalidate():
                calls.append(1)
                if len(calls) == fail_at:
                    raise MigrationDenied()
            with self.assertRaises(MigrationDenied):
                self.attach(revalidate=revalidate)
            self.unchanged()

    def test_stale_row_version_fails_without_insert(self):
        with self.connect() as connection:
            connection.execute("UPDATE cuevion_account.users SET row_version = row_version + 1 WHERE user_id = %s", (rows.USER_ID,))
        with self.assertRaises(MigrationDenied):
            self.attach()
        self.assertEqual(len(self.reader.read(self.session).identities), 1)

    def test_disabled_old_identity_and_changed_epoch_fail_without_insert(self):
        for sql in (
            "UPDATE cuevion_account.authentication_identities SET status = 'disabled', row_version = row_version + 1",
            "UPDATE cuevion_account.users SET security_epoch = security_epoch + 1, row_version = row_version + 1",
        ):
            self.setUp()
            with self.connect() as connection:
                connection.execute(sql)
            with self.assertRaises(MigrationDenied):
                self.attach()
            with self.connect() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM cuevion_account.authentication_identities").fetchone(), (1,))

    def test_existing_subject_global_claim_and_conflicting_identity_rejected(self):
        for subject in (fixtures.NEW_SUBJECT, "auth0|conflicting-database"):
            self.setUp()
            with self.connect() as connection:
                connection.execute("INSERT INTO cuevion_account.authentication_identities "
                    "SELECT schema_version, %s, user_id, issuer, %s, 'oidc', status, verified_email_id, "
                    "created_at, last_used_at, row_version FROM cuevion_account.authentication_identities",
                    ("aid_" + rows._b64(9), subject))
            before = self.reader.read(self.session)
            with self.assertRaises(MigrationDenied):
                self.attach()
            self.assertEqual(self.reader.read(self.session).identities, before.identities)

    def test_extra_user_membership_or_email_rows_fail_closed(self):
        with self.connect() as connection:
            connection.execute("INSERT INTO cuevion_account.users "
                "SELECT schema_version, %s, 'suspended', NULL, display_name, security_epoch, created_at, updated_at, row_version "
                "FROM cuevion_account.users", (rows.OTHER_USER_ID,))
        with self.assertRaises(MigrationDenied):
            self.attach()
        self.assertEqual(len(self.reader.read(self.session).identities), 1)

    def test_identity_collision_rolls_back(self):
        with patch.object(repository.account_record_ids, "generate_authentication_identity_id_candidate",
                          return_value=rows.IDENTITY_ID), self.assertRaises(MigrationDenied):
            self.attach()
        self.unchanged()

    def test_target_claimed_by_other_user_never_rebound(self):
        with self.connect() as connection:
            connection.execute("INSERT INTO cuevion_account.users "
                "SELECT schema_version, %s, 'suspended', NULL, display_name, security_epoch, created_at, updated_at, row_version "
                "FROM cuevion_account.users", (rows.OTHER_USER_ID,))
            connection.execute("INSERT INTO cuevion_account.authentication_identities "
                "SELECT schema_version, %s, %s, issuer, %s, 'oidc', status, NULL, created_at, last_used_at, row_version "
                "FROM cuevion_account.authentication_identities", ("aid_" + rows._b64(9), rows.OTHER_USER_ID, fixtures.NEW_SUBJECT))
        with self.assertRaises(MigrationDenied):
            self.attach()
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT user_id FROM cuevion_account.authentication_identities "
                "WHERE subject = %s", (fixtures.NEW_SUBJECT,)).fetchone(), (rows.OTHER_USER_ID,))

    def test_table_lock_blocks_concurrent_unexpected_insert_until_commit(self):
        attempted = threading.Event()
        ready = threading.Event()
        def insert_extra():
            with self.connect() as connection:
                ready.set()
                attempted.set()
                connection.execute("INSERT INTO cuevion_account.users "
                    "SELECT schema_version, %s, 'suspended', NULL, display_name, security_epoch, created_at, updated_at, row_version "
                    "FROM cuevion_account.users", (rows.OTHER_USER_ID,))
        calls = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = None
            def revalidate():
                nonlocal job
                calls.append(1)
                if len(calls) == 1:
                    job = pool.submit(insert_extra)
                    self.assertTrue(ready.wait(5))
                    self.assertTrue(attempted.wait(5))
                self.assertFalse(job.done())
            self.attach(revalidate=revalidate)
            job.result(timeout=5)
        # A later new user is a later operation, not a phantom in the migration.
        self.assertEqual(len(self.reader.read(self.session).identities), 2)

    def test_writer_cannot_fall_back_to_reader_credentials(self):
        for environment in ({}, {"CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": "same",
                                 "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": "same"}):
            with self.assertRaises(MigrationDenied):
                repository.build_runtime_repository(environment)
