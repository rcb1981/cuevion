"""Actual PostgreSQL privileges, zero data changes, and lock release.

All DDL/grants/fixture inserts below are confined to a disposable cluster.
The bundled local PostgreSQL lacks SSL support. Successful SQL probes use an
explicit test-only TLS attestation; the real plaintext-rejection test uses the
actual driver TLS state. The separate unit suite covers the strict connector.
The diagnostic's own connection executes only the recorded fixed probe SQL.
No application DSN or Production service is used.
"""

from types import SimpleNamespace
import time
import unittest

import psycopg

from cuevion_db import passkey_writer_capability as capability
from cuevion_db import identity_inventory_diagnostic as inventory
from tests.cuevion_db import test_postgresql_owner_passkey_migration as original
from tests.cuevion_migration import test_writer_capability as unit


class RecordedConnection:
    def __init__(self, connection, *, fail_after_lock=False, attest_tls=True):
        self.connection = connection
        self.events = []
        self.fail_after_lock = fail_after_lock
        self.pgconn = SimpleNamespace(ssl_in_use=True) if attest_tls else connection.pgconn

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def cursor(self):
        cursor = self.connection.cursor()
        owner = self
        class Cursor:
            def execute(self, sql):
                owner.events.append(sql)
                result = cursor.execute(sql)
                if owner.fail_after_lock and sql == capability._EXACT_LOCK_SQL:
                    raise RuntimeError("synthetic failure after actual lock acquisition")
                return result
            def fetchone(self):
                return cursor.fetchone()
            def close(self):
                owner.events.append("cursor.close")
                cursor.close()
        return Cursor()

    def rollback(self):
        self.events.append("rollback")
        self.connection.rollback()

    def close(self):
        self.events.append("close")
        self.connection.close()


class PostgreSQLWriterCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original.PostgreSQLOwnerMigrationTests.setUpClass.__func__(cls)
        with cls.connect() as connection:
            connection.execute("CREATE ROLE writer_probe NOLOGIN")

    tearDownClass = classmethod(original.PostgreSQLOwnerMigrationTests.tearDownClass.__func__)
    connect = classmethod(original.PostgreSQLOwnerMigrationTests.connect.__func__)

    def setUp(self):
        original.PostgreSQLOwnerMigrationTests.setUp(self)
        self.recorded = None
        with self.connect() as connection:
            connection.execute("GRANT USAGE ON SCHEMA cuevion_account TO writer_probe")

    def grants(self, *, select=True, lock=True, insert=True):
        # Only synthetic test roles/tables in this class's temporary cluster.
        with self.connect() as connection:
            if select:
                connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA cuevion_account TO writer_probe")
            if lock:
                connection.execute("GRANT UPDATE ON ALL TABLES IN SCHEMA cuevion_account TO writer_probe")
            if insert:
                connection.execute("GRANT INSERT ON cuevion_account.authentication_identities TO writer_probe")

    def writer_connect(self, _parsed, *, attest_tls=True, fail_after_lock=False):
        # Ignores the synthetic parsed URL; it cannot connect outside this fixture.
        connection = psycopg.connect(host=self.temp.name,port=55439,dbname="postgres",user="postgres",
            connect_timeout=3,
            options="-c timezone=UTC -c role=writer_probe",autocommit=False)
        self.recorded = RecordedConnection(connection,fail_after_lock=fail_after_lock,attest_tls=attest_tls)
        return self.recorded

    def assert_unchanged_and_locks_released(self):
        original.PostgreSQLOwnerMigrationTests.unchanged(self)
        with self.connect() as connection:
            # Conflicts with every table lock: NOWAIT proves none escaped probe cleanup.
            connection.execute(capability._LOCK_SQL.replace("SHARE ROW EXCLUSIVE","ACCESS EXCLUSIVE")+" NOWAIT")
            for table,columns,_,order in inventory._TABLES:
                actual=connection.execute(f"SELECT {', '.join(columns)} FROM cuevion_account.{table} ORDER BY {order}").fetchall()
                self.assertEqual(actual,self.raw[table])
        self.assertTrue(self.recorded.connection.closed)
        self.assertEqual(self.recorded.events[-3:], ["rollback","cursor.close","close"])
        for sql in self.recorded.events:
            self.assertNotIn(sql.split()[0].upper(),{"INSERT","UPDATE","DELETE","TRUNCATE","CREATE","ALTER","DROP","COMMIT"})

    def test_real_sql_capabilities_ready_without_any_data_change(self):
        self.grants()
        result=capability.probe(unit.ENV,connect=self.writer_connect)
        self.assertEqual(result["classification"],"ready",result)
        self.assertTrue(result["tlsInUse"])
        self.assertTrue(result["transactionRolledBack"])
        self.assertTrue(result["exactPhase1LockAcquired"])
        self.assertEqual(self.recorded.events[-4:], [capability._EXACT_LOCK_SQL,"rollback","cursor.close","close"])
        self.assert_unchanged_and_locks_released()

    def test_real_select_privilege_failure(self):
        self.grants(select=False)
        result=capability.probe(unit.ENV,connect=self.writer_connect)
        self.assertEqual(result["classification"],"writer_select_privilege_missing")
        self.assert_unchanged_and_locks_released()

    def test_real_exact_lock_privilege_failure(self):
        self.grants(lock=False)
        result=capability.probe(unit.ENV,connect=self.writer_connect)
        self.assertEqual(result["classification"],"writer_lock_privilege_missing")
        self.assertTrue(result["selectAuthenticationIdentities"])
        self.assertTrue(result["insertIdentityPrivilege"])
        self.assert_unchanged_and_locks_released()

    def test_real_insert_privilege_missing_without_test_insert(self):
        self.grants(insert=False)
        result=capability.probe(unit.ENV,connect=self.writer_connect)
        self.assertEqual(result["classification"],"writer_insert_privilege_missing")
        self.assertTrue(result["exactPhase1LockAcquired"])
        self.assert_unchanged_and_locks_released()

    def test_active_work_and_ddl_are_not_waited_on_or_misclassified_as_privilege_failure(self):
        self.grants()
        for mode in ("ROW EXCLUSIVE","ACCESS EXCLUSIVE"):
            with self.subTest(mode=mode), self.connect() as blocker:
                blocker.execute(f"LOCK TABLE cuevion_account.users IN {mode} MODE")
                started=time.monotonic()
                result=capability.probe(unit.ENV,connect=self.writer_connect)
                elapsed=time.monotonic()-started
                self.assertEqual(result["classification"],"unavailable")
                self.assertLess(elapsed,1.0)  # Includes a fresh local connection; SQL uses NOWAIT.
                self.assertTrue(result["transactionRolledBack"])
            self.assert_unchanged_and_locks_released()

    def test_failure_after_actual_strong_lock_releases_it_and_changes_no_rows(self):
        self.grants()
        result=capability.probe(unit.ENV,connect=lambda parsed:self.writer_connect(parsed,fail_after_lock=True))
        self.assertEqual(result["classification"],"unavailable")
        self.assert_unchanged_and_locks_released()

    def test_actual_plaintext_connection_is_rejected_before_sql_and_closed(self):
        self.grants()
        result=capability.probe(unit.ENV,connect=lambda parsed:self.writer_connect(parsed,attest_tls=False))
        self.assertEqual(result["classification"],"writer_tls_invalid")
        self.assertFalse(result["tlsInUse"])
        self.assertEqual(self.recorded.events,["rollback","close"])
        self.assertTrue(self.recorded.connection.closed)
        original.PostgreSQLOwnerMigrationTests.unchanged(self)
