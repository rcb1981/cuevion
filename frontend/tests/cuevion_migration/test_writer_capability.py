"""Sanitized capability classification and unchanged owner/session boundaries."""

from dataclasses import replace
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from psycopg import errors, pq

from api.auth import session_store
from cuevion_db import passkey_writer_capability as db
from cuevion_migration import writer_capability as diagnostic
from tests.cuevion_migration import test_owner_passkey_migration as fixtures


WRITER = "postgresql://private_writer:private_password@private-host.test/private_database?sslmode=require&channel_binding=require"
READER = "postgresql://private_reader:private_password@private-host.test/private_database?sslmode=require&channel_binding=require"
ENV = {"CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": WRITER,
       "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": READER}


class Connection:
    def __init__(self, *, tls=True, autocommit=False, fail_sql=None, error=None, insert=True,
                 fail_cleanup=None):
        self.autocommit = autocommit
        self.pgconn = SimpleNamespace(ssl_in_use=tls, finish=lambda: self.events.append("finish"))
        self.info = SimpleNamespace(transaction_status=pq.TransactionStatus.IDLE)
        self.fail_sql, self.error, self.insert = fail_sql, error, insert
        self.fail_cleanup = fail_cleanup
        self.events = []

    def cursor(self):
        connection = self
        class Cursor:
            def execute(self, sql):
                connection.events.append(sql)
                if sql == connection.fail_sql:
                    raise connection.error
            def fetchone(self):
                return (connection.insert,)
            def close(self):
                connection.events.append("cursor.close")
                if connection.fail_cleanup == "cursor.close":
                    raise RuntimeError(WRITER)
        return Cursor()

    def rollback(self):
        self.events.append("rollback")
        if self.fail_cleanup == "rollback":
            raise RuntimeError(WRITER)

    def close(self):
        self.events.append("close")
        if self.fail_cleanup == "close":
            raise RuntimeError(WRITER)


class ProbeTests(unittest.TestCase):
    def check(self, expected, connection=None, environment=None, connect=None):
        connection = connection or Connection()
        result = db.probe(ENV if environment is None else environment,
                          connect=connect or (lambda _: connection))
        self.assertEqual(result["classification"], expected, result)
        self.assertIn(result["classification"], db.CLASSIFICATIONS)
        self.assertTrue(all(type(v) is bool for k,v in result.items() if k != "classification"))
        for value in (WRITER, READER, "private_writer", "private_password", "private_database", "private-host"):
            self.assertNotIn(value, json.dumps(result))
        if result["writerConnectionEstablished"]:
            self.assertIn("rollback", connection.events)
            self.assertIn(connection.events[-1], ("close", "finish"))
        return result, connection

    def test_configuration_missing_equal_and_malformed_never_connect(self):
        for environment, classification in (
            ({}, "writer_configuration_missing"),
            ({**ENV, "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": ""}, "writer_configuration_missing"),
            ({**ENV, "CUEVION_AUTH_ACCOUNT_READER_DATABASE_URL": ""}, "writer_configuration_missing"),
            ({**ENV, "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": READER}, "writer_not_distinct"),
            ({**ENV, "CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL": "malformed"}, "writer_configuration_missing"),
        ):
            with self.subTest(classification=classification):
                self.check(classification, environment=environment,
                           connect=lambda _: self.fail("configuration failure opened writer"))

    def test_connection_failure_returns_no_exception_details(self):
        def fail(_):
            raise RuntimeError(WRITER + " role=private_writer SQLSTATE=08006")
        self.check("writer_connection_failed", connect=fail)

    def test_tls_autocommit_and_transaction_state_fail_closed(self):
        for connection, classification in ((Connection(tls=False), "writer_tls_invalid"),
                                            (Connection(autocommit=True), "unavailable")):
            self.check(classification, connection)
            self.assertEqual(connection.events, ["rollback", "close"])
        connection = Connection()
        connection.info.transaction_status = pq.TransactionStatus.INTRANS
        self.check("unavailable", connection)

    def test_each_select_failure_is_classified_without_raw_error(self):
        for table, flag in db._TABLES:
            with self.subTest(table=table):
                result, _ = self.check("writer_select_privilege_missing", Connection(
                    fail_sql=f"SELECT * FROM cuevion_account.{table} LIMIT 0",
                    error=errors.InsufficientPrivilege(WRITER)))
                self.assertFalse(result[flag])
                self.assertFalse(result["exactPhase1LockAcquired"])

    def test_lock_privilege_failure_and_contention_are_distinct(self):
        self.check("writer_lock_privilege_missing", Connection(fail_sql=db._EXACT_LOCK_SQL,
                    error=errors.InsufficientPrivilege(WRITER)))
        for sql in (db._READ_LOCK_SQL, db._EXACT_LOCK_SQL):
            for error in (errors.LockNotAvailable(WRITER), errors.QueryCanceled(WRITER),
                          errors.UndefinedTable(WRITER)):
                self.check("unavailable", Connection(fail_sql=sql, error=error))

    def test_insert_privilege_is_introspection_only(self):
        result, _ = self.check("writer_insert_privilege_missing", Connection(insert=False))
        self.assertTrue(result["exactPhase1LockAcquired"])
        self.assertFalse(result["insertIdentityPrivilege"])
        self.check("unavailable", Connection(insert=None))

    def test_ready_has_closed_sql_list_and_rolls_back_immediately_after_lock(self):
        result, connection = self.check("ready")
        self.assertTrue(all(v for k,v in result.items() if k != "classification"))
        self.assertEqual(connection.events, [
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL statement_timeout = '1000ms'", "SET LOCAL lock_timeout = '200ms'",
            "SET LOCAL idle_in_transaction_session_timeout = '2000ms'", db._READ_LOCK_SQL,
            *[f"SELECT * FROM cuevion_account.{table} LIMIT 0" for table, _ in db._TABLES],
            db._INSERT_PRIVILEGE_SQL, db._EXACT_LOCK_SQL, "rollback", "cursor.close", "close"])
        for sql in connection.events:
            self.assertNotIn(sql.split()[0].upper(), {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "ALTER", "DROP", "COMMIT"})

    def test_every_statement_failure_and_cleanup_failure_still_close(self):
        _, successful = self.check("ready")
        for sql in successful.events[:-3]:
            with self.subTest(sql=sql):
                self.check("unavailable", Connection(fail_sql=sql, error=RuntimeError(WRITER)))
        for operation in ("rollback", "cursor.close", "close"):
            self.check("unavailable", Connection(fail_cleanup=operation))

    def test_native_close_fallback_on_high_level_close_failure(self):
        result, connection = self.check("unavailable", Connection(fail_cleanup="close"))
        self.assertEqual(connection.events[-2:], ["close", "finish"])
        self.assertTrue(result["connectionClosed"])

    def test_default_connector_preserves_url_tls_options_and_non_autocommit(self):
        connection = Connection()
        with patch.object(db.account_authority, "_default_connect", return_value=connection) as connect:
            result = db.probe(ENV)
        self.assertEqual(result["classification"], "ready")
        connect.assert_called_once_with(WRITER, autocommit=False, connect_timeout=3)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.harness = fixtures.MigrationTests()
        self.harness.setUp()
        self.harness.environment.update(ENV)
        self.connection = Connection()
        self.connections = 0

    def connect(self, _):
        self.connections += 1
        return self.connection

    def response(self, **overrides):
        h = self.harness
        args = dict(method="GET", raw_headers=h.headers, path=diagnostic.ROUTE,
            environment=h.environment, now=fixtures.NOW, reader_factory=lambda _: h.reader,
            transport_factory=lambda _: h.memory, connect=self.connect)
        args.update(overrides)
        before, snapshot = dict(h.memory.values), h.reader.snapshot
        h.memory.commands.clear()
        response = diagnostic.capability_response(**args)
        self.assertEqual(before, h.memory.values)
        self.assertEqual(snapshot, h.reader.snapshot)
        self.assertTrue(all(c[0] in ("GET", "EVAL") for c in h.memory.commands))
        self.assertFalse(any(k == "Set-Cookie" for k,v in response.headers))
        self.assertIn(("Cache-Control", "no-store"), response.headers)
        result = json.loads(response.body)
        self.assertIn(result["classification"], db.CLASSIFICATIONS)
        self.assertTrue(all(type(v) is bool or v in db.CLASSIFICATIONS or v == diagnostic._TARGET_STATE
                            for v in result.values()))
        return response, result

    def test_unauthenticated_member_admin_denied_before_writer(self):
        response, _ = self.response(raw_headers=self.harness.headers[:-1])
        self.assertEqual(response.status, 401)
        for role in ("member", "admin"):
            self.harness.memory.values[self.harness.session_key] = session_store._encode_record(
                replace(self.harness.session, workspace_role=role))
            response, _ = self.response()
            self.assertEqual(response.status, 403)
        self.assertEqual(self.connections, 0)

    def test_origin_host_methods_queries_bodies_and_preview_denied(self):
        h = self.harness
        for key,value in (("Origin", "https://foreign.test"), ("Host", "foreign.test"),
                          ("Sec-Fetch-Site", "cross-site")):
            response, _ = self.response(raw_headers=tuple((k,value if k==key else v) for k,v in h.headers))
            self.assertEqual(response.status, 403)
        for kwargs in ({"method":"POST"}, {"path":diagnostic.ROUTE+"?subject=auth0|other"},
                       {"raw_headers":h.headers+(("Content-Length","1"),)},
                       {"raw_headers":h.headers+(("Transfer-Encoding","chunked"),)},
                       {"environment":{**h.environment,"VERCEL_ENV":"preview"}}):
            response, _ = self.response(**kwargs)
            self.assertNotEqual(response.status, 200)
        self.assertEqual(self.connections, 0)

    def test_initial_owner_and_pending_invitation_envelope_enforced(self):
        h = self.harness
        h.reader.snapshot = replace(fixtures.snapshot(), emails=(replace(fixtures.snapshot().emails[0],
            verification_source="team-invite-oidc:v1:"+"a"*64),))
        response, result = self.response()
        self.assertEqual(response.status, 403)
        self.assertEqual(result["classification"], "owner_envelope_changed")
        h.reader.snapshot = fixtures.snapshot()
        with patch.object(fixtures.migration.diagnostic, "_read_team_and_config", return_value=({
            "pendingTeamInvitations":1,"suspendedOrIncompleteMemberProvisioning":0,
            "ownerRecipientInvitationPresent":False,"currentSessionInviteContinuationPresent":False},[])):
            self.assertEqual(self.response()[0].status, 403)
        self.assertEqual(self.connections, 0)

    def test_configuration_and_all_capabilities_ready_are_sanitized(self):
        for environment, classification in ((self.harness.environment,"ready"),
            ({**self.harness.environment,"CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL":""},"writer_configuration_missing"),
            ({**self.harness.environment,"CUEVION_AUTH_ACCOUNT_WRITER_DATABASE_URL":READER},"writer_not_distinct")):
            response, result = self.response(environment=environment)
            self.assertEqual(response.status, 200)
            self.assertEqual(result["classification"],classification)
            self.assertEqual(result["targetState"],diagnostic._TARGET_STATE)
            self.assertNotIn("targetIdentityAlreadyCanonical",result)
            self.assertTrue(result["currentOwnerStillSingleCanonicalIdentity"])
            self.assertTrue(result["ownerEnvelopeStillSafeForRetry"])
            for value in (WRITER, READER, fixtures.SUBJECT, fixtures.rows.EMAIL, self.harness.session.session_id,
                          fixtures.tokens.SESSION_SECRET, "private_writer", "private_database"):
                self.assertNotIn(value.encode(),response.body)

    def test_consumed_candidate_is_not_retried_changed_or_inferred(self):
        self.harness.start()
        self.harness.denied_callback(overrides={"email_verified":False})
        before = dict(self.harness.memory.values)
        _, result = self.response()
        self.assertEqual(result["targetState"], diagnostic._TARGET_STATE)
        self.assertEqual(before,self.harness.memory.values)
        self.assertEqual(self.harness.repository.calls,[])

    def test_second_canonical_identity_closes_retry_without_claiming_target_match(self):
        h = self.harness
        old = h.reader.snapshot.identities[0]
        new = replace(old, identity_id="aid_"+fixtures.rows._b64(9), subject=fixtures.NEW_SUBJECT,
                      method=fixtures.models.AuthenticationMethod.OIDC)
        h.reader.snapshot = replace(h.reader.snapshot, identities=(old,new))
        response,result = self.response()
        self.assertEqual(response.status,200)
        self.assertEqual(result["classification"],"owner_envelope_changed")
        self.assertFalse(result["currentOwnerStillSingleCanonicalIdentity"])
        self.assertFalse(result["ownerEnvelopeStillSafeForRetry"])
        self.assertNotIn("targetIdentityAlreadyCanonical",result)
        self.assertEqual(self.connections,0)

    def test_canonical_change_after_probe_denies_and_writer_is_already_closed(self):
        original = self.harness.reader.read
        calls = []
        def read(session):
            calls.append(1)
            result = original(session)
            if len(calls) >= 3:
                self.assertTrue(self.connection.events[-1] == "close")
                user = replace(result.users[0], row_version=result.users[0].row_version+1)
                owner = result.authority
                authority = fixtures.contract.CurrentAccountAuthority(user, owner.primary_verified_email,
                    owner.authentication_identity, owner.workspace, owner.workspace_membership)
                result = replace(result, users=(user,), authority=authority)
            return result
        with patch.object(self.harness.reader,"read",side_effect=read):
            response,result = self.response()
        self.assertEqual(response.status,403)
        self.assertEqual(result["classification"],"owner_envelope_changed")

    def test_browser_get_without_origin_and_unknown_handler_methods(self):
        headers=tuple((k,v) for k,v in self.harness.headers if k != "Origin")
        self.assertEqual(self.response(raw_headers=headers)[0].status,200)
        handler=importlib.import_module("api.auth.passkey-migration.writer-capability").handler
        fake=fixtures.AdapterHandler("CUSTOM",diagnostic.ROUTE,self.harness.headers)
        handler.send_error(fake,501,WRITER,WRITER)
        self.assertEqual(fake.status,405)
        self.assertEqual(json.loads(fake.wfile.getvalue()),{"classification":"unavailable"})

    def test_response_time_is_rechecked_after_final_authority_reads(self):
        with patch.object(diagnostic.time,"time",side_effect=[fixtures.NOW,fixtures.NOW+1,fixtures.NOW+21]):
            response,result = self.response(now=None)
        self.assertEqual(response.status,503)
        self.assertEqual(result,{"classification":"unavailable"})
        self.assertEqual(self.connection.events[-1],"close")
