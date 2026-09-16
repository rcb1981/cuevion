"""Offline security and real disposable Redis checks for the temporary route."""

import base64
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psycopg

from api.auth import auth0_flow, http, session_store
from api.auth.test_auth_routes import AdapterHandler, MemoryCommands
from api.collaboration import owner_request_security as security
from api.team import authority as team
from cuevion_auth import identity_inventory_diagnostic as diagnostic
from cuevion_db import identity_inventory_diagnostic as database
from cuevion_db import postgresql_current_account_repository as canonical
from cuevion_db import postgresql_team_invitee_repository as invitee
from tests.cuevion_db import test_postgresql_current_account_repository as fixtures


NOW = 1_800_000_000
ISSUER = "https://" + auth0_flow.AUTH0_DOMAIN + "/"
SUBJECT = "email|diagnostic-owner"
SECRET = "diagnostic-session-key-" * 3
ENV = {"VERCEL_ENV": "production", "CUEVION_AUTH_SESSION_SECRET": SECRET}
CONFIG_KEY = "cuevion:user:v1:" + fixtures.EMAIL


def rows():
    return {
        "users": [fixtures._user_segment()],
        "verified_emails": [fixtures._email_segment(verification_source="cuevion_first_account_operator_v1")],
        "authentication_identities": [fixtures._identity_segment(issuer=ISSUER, subject=SUBJECT)],
        "workspaces": [fixtures._workspace_segment()],
        "workspace_memberships": [fixtures._membership_segment(role="owner")],
    }


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, sql, parameters=None):
        db = self.connection
        db.statements.append((sql, parameters))
        if db.factory.fail_sql:
            raise RuntimeError("DO_NOT_RETURN_DATABASE_SECRET")
        if sql == canonical._SET_TRANSACTION_SQL:
            db.read_only = True
            return
        if sql in ("SET LOCAL statement_timeout = '5000ms'", "SET LOCAL lock_timeout = '1000ms'"):
            return
        if not db.read_only:
            raise AssertionError("query outside read-only transaction")
        if sql == canonical._SELECT_CURRENT_ACCOUNT_BY_IDENTITY_SQL + " LIMIT 2":
            self.rows = [db.tables[table][0] for table in db.tables]
            self.rows = [sum(self.rows, ())] * db.factory.authority_copies
            return
        for table, columns, _, order in database._TABLES:
            if sql == (f"SELECT {', '.join(columns)} FROM cuevion_account.{table} "
                       f"ORDER BY {order} LIMIT {database.MAX_ACCOUNT_ROWS + 1}"):
                self.rows = list(db.tables[table])
                return
        raise AssertionError("unexpected SQL")

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.connection.cursor_closed = True


class Connection:
    def __init__(self, factory):
        self.factory = factory
        self.tables = deepcopy(factory.tables)
        self.autocommit = factory.autocommit
        self.info = SimpleNamespace(transaction_status=factory.transaction_status)
        self.read_only = self.closed = self.cursor_closed = False
        self.rollbacks = 0
        self.statements = []

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.rollbacks += 1
        if self.factory.fail_cleanup:
            raise RuntimeError("DO_NOT_RETURN_CLEANUP_SECRET")

    def close(self):
        self.closed = True

    def commit(self):
        raise AssertionError("diagnostic must never commit")


class Factory:
    def __init__(self):
        self.tables = rows()
        self.connections = []
        self.fail_sql = self.fail_cleanup = self.autocommit = False
        self.authority_copies = 1
        self.transaction_status = psycopg.pq.TransactionStatus.IDLE
        self.on_connect = None

    def __call__(self):
        if self.on_connect:
            self.on_connect(len(self.connections))
        connection = Connection(self)
        self.connections.append(connection)
        return connection


class ReadTransport:
    def __init__(self):
        self.values = {}
        self.commands = []
        self.on_read = None

    def __call__(self, command):
        self.commands.append(command)
        if self.on_read:
            self.on_read(len(self.commands))
        if command[:2] != ["EVAL", diagnostic._SNAPSHOT_LUA]:
            raise AssertionError("unapproved Redis operation")
        keys = command[3:3 + command[2]]
        return {"result": [self.values.get(key) for key in keys]}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.db = Factory()
        self.kv = ReadTransport()
        memory = MemoryCommands()
        self.record, cookie = session_store.create_server_session(
            session_store.AuthSessionStore(memory), secret=SECRET,
            user_id=fixtures.USER_ID, workspace_id=fixtures.WORKSPACE_ID,
            security_epoch=3, issuer=ISSUER, subject=SUBJECT, now=NOW - 100)
        self.cookie = cookie.split(";", 1)[0]
        self.kv.values.update(memory.values)
        self.session_key = next(iter(memory.values))
        self.headers = (("Host", "app.cuevion.com"), ("Sec-Fetch-Site", "same-origin"),
                        ("Cookie", self.cookie))

    def response(self, *, method="GET", headers=None, path=diagnostic.ROUTE, environment=None, now=NOW):
        return diagnostic.diagnostic_response(method, self.headers if headers is None else headers, path,
            environment=ENV if environment is None else environment, now=now,
            reader_factory=lambda _: database.PostgreSQLIdentityInventoryDiagnostic(self.db),
            transport_factory=lambda _: self.kv)

    def assert_denied(self, response, status=None):
        self.assertNotEqual(response.status, 200)
        if status is not None:
            self.assertEqual(response.status, status)
        self.assertEqual(json.loads(response.body), {
            "error": {"code": "identity_diagnostic_unavailable", "message": "Identity diagnostic is unavailable."}})
        self.assertIn(("Cache-Control", "no-store"), response.headers)
        self.assertFalse(any(key.lower() == "set-cookie" for key, _ in response.headers))
        for sensitive in (fixtures.USER_ID, fixtures.EMAIL, SUBJECT, self.cookie, SECRET):
            self.assertNotIn(sensitive.encode(), response.body)

    def test_unauthenticated_denied_before_store_access(self):
        self.assert_denied(self.response(headers=self.headers[:2]), 401)
        self.assertEqual(self.kv.commands, [])
        self.assertEqual(self.db.connections, [])

    def test_member_and_admin_denied_before_inventory_queries(self):
        for role in ("member", "admin"):
            with self.subTest(role=role):
                self.setUp()
                self.record = replace(self.record, workspace_role=role)
                self.kv.values[self.session_key] = session_store._encode_record(self.record)
                self.db.tables["workspace_memberships"] = [fixtures._membership_segment(role=role)]
                self.assert_denied(self.response(), 403)
                self.assertEqual(len(self.db.connections[0].statements), 4)

    def test_owner_allowed_and_response_fields_come_from_canonical_graph(self):
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        result = json.loads(response.body)
        self.assertEqual(result["owner"], {
            "userId": fixtures.USER_ID, "email": fixtures.EMAIL,
            "workspaceId": fixtures.WORKSPACE_ID, "workspaceRole": "owner",
            "issuer": ISSUER, "subject": SUBJECT, "authenticationMethod": "oidc",
            "verificationProvenance": "initial-account-operator", "membershipState": "active",
            "isInitialOwner": True, "teamInviteProvenanceExists": False,
            "collaborationIssuerSubjectBindingsExist": False,
        })
        self.assertEqual(result["counts"], {"users": 1, "ownerMemberships": 1, "adminMemberships": 0,
            "memberMemberships": 0, "authenticationIdentities": 1, "activeWorkspaces": 1,
            "pendingTeamInvitations": 0, "suspendedOrIncompleteMemberProvisioning": 0})
        self.assertTrue(result["subjectDependentState"]["currentAuthenticatedSessionPresent"])
        self.assertIn("current authenticated session", result["coverage"]["sessions"])
        self.assertIn(("Cache-Control", "no-store"), response.headers)

    def test_session_missing_expired_invalid_binding_and_security_epoch_are_read_only(self):
        variants = (None, replace(self.record, expires_at=NOW - 1),
                    replace(self.record, binding_digest="X" * 43), replace(self.record, security_epoch=2))
        for record in variants:
            with self.subTest(record=record is None):
                self.setUp()
                if record is None:
                    self.kv.values.pop(self.session_key)
                else:
                    self.kv.values[self.session_key] = session_store._encode_record(record)
                before = deepcopy(self.kv.values)
                self.assert_denied(self.response())
                self.assertEqual(self.kv.values, before)

    def test_canonical_subject_revocation_and_workspace_mismatch_denied(self):
        changes = (
            ("authentication_identities", fixtures._identity_segment(issuer=ISSUER, subject="auth0|different")),
            ("authentication_identities", fixtures._identity_segment(issuer=ISSUER, subject=SUBJECT, status="revoked")),
            ("workspaces", fixtures._workspace_segment(workspace_id=fixtures.OTHER_WORKSPACE_ID)),
            ("workspace_memberships", fixtures._membership_segment(role="owner", status="suspended")),
            ("users", fixtures._user_segment(status="disabled")),
        )
        for table, row in changes:
            with self.subTest(table=table):
                self.setUp()
                self.db.tables[table] = [row]
                self.assert_denied(self.response())

    def test_get_only_and_no_store_on_all_denials(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT", "get"):
            self.assert_denied(self.response(method=method), 405)
        self.assertEqual(self.kv.commands, [])

    def test_same_origin_host_and_duplicate_headers_fail_closed(self):
        variants = (
            (("Host", "evil.example"), ("Sec-Fetch-Site", "same-origin")),
            (("Host", "app.cuevion.com"),),
            (("Host", "app.cuevion.com"), ("Sec-Fetch-Site", "cross-site")),
            self.headers + (("Origin", "https://evil.example"),),
            self.headers + (("Sec-Fetch-Site", "same-origin"),),
            self.headers + (("Cookie", self.cookie),),
            self.headers + (("X-Forwarded-Host", "evil.example"),),
            self.headers + (("Host", "app.cuevion.com"),),
        )
        for headers in variants:
            self.assert_denied(self.response(headers=headers))
        self.assertEqual(self.kv.commands, [])
        self.assertEqual(self.response(headers=self.headers + (("Origin", "https://app.cuevion.com"),)).status, 200)

    def test_no_query_body_or_browser_authority_override(self):
        self.assert_denied(self.response(path=diagnostic.ROUTE + "?role=owner&userId=arbitrary"), 400)
        for header in (("Content-Length", "2"), ("Transfer-Encoding", "chunked")):
            self.assert_denied(self.response(headers=self.headers + (header,)), 400)
        payload = json.loads(self.response(headers=self.headers + (("X-User-Id", "arbitrary"),
            ("X-Workspace-Id", "arbitrary"), ("X-Role", "admin"))).body)
        self.assertEqual(payload["owner"]["userId"], fixtures.USER_ID)

    def test_production_only(self):
        for target in (None, "preview", "development", "Production"):
            self.assert_denied(self.response(environment={**ENV, "VERCEL_ENV": target}), 404)
        self.assertEqual(self.kv.commands, [])

    def test_ambiguous_identity_and_multiple_workspaces_fail_closed(self):
        self.db.authority_copies = 2
        self.assert_denied(self.response(), 503)
        self.setUp()
        self.db.tables["workspaces"].append(fixtures._workspace_segment(workspace_id=fixtures.OTHER_WORKSPACE_ID))
        self.db.tables["workspace_memberships"].append(fixtures._membership_segment(
            workspace_id=fixtures.OTHER_WORKSPACE_ID, role="owner"))
        self.assert_denied(self.response(), 503)

    def test_repository_failure_and_cleanup_failure_are_generic(self):
        for attr in ("fail_sql", "fail_cleanup", "autocommit"):
            self.setUp()
            setattr(self.db, attr, True)
            self.assert_denied(self.response(), 503)
            self.assertTrue(all(c.closed and c.rollbacks for c in self.db.connections))

    def test_no_write_or_mutation_helper_called(self):
        before = deepcopy(self.kv.values)
        forbidden = [patch.object(session_store.AuthSessionStore, name, side_effect=AssertionError(name))
                     for name in ("delete", "put", "consume_transaction", "put_team_invite_continuation",
                                  "complete_team_invite_continuation")]
        forbidden += [patch.object(team.RuntimeTeamAuthority, name, side_effect=AssertionError(name))
                      for name in ("list_pending_invitations", "_atomic", "accept_invitation")]
        for blocker in forbidden:
            blocker.start()
            self.addCleanup(blocker.stop)
        self.assertEqual(self.response().status, 200)
        self.assertEqual(self.kv.values, before)
        self.assertEqual(set(re.findall(r"redis.call\('([A-Z]+)'", diagnostic._SNAPSHOT_LUA)), {"GET", "STRLEN"})
        self.assertEqual(len(self.db.connections), 2)
        for connection in self.db.connections:
            self.assertEqual(connection.statements[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.assertTrue(connection.closed and connection.cursor_closed)
            self.assertEqual(connection.rollbacks, 1)
            self.assertEqual(sum(sql.startswith("SELECT ") for sql, _ in connection.statements), 5)
            self.assertFalse(any(re.search(r"\b(UPDATE|INSERT|DELETE|FOR SHARE|FOR UPDATE)\b", sql)
                                 for sql, _ in connection.statements))

    def test_sql_graph_change_between_snapshots_denies_all_identity_data(self):
        def on_connect(number):
            if number == 1:
                self.db.tables["users"] = [fixtures._user_segment(row_version=99)]
        self.db.on_connect = on_connect
        self.assert_denied(self.response(), 503)

    def test_kv_change_or_session_revocation_mid_request_denied(self):
        def mutate(number):
            if number == 5:
                self.kv.values.pop(self.session_key)
        self.kv.on_read = mutate
        self.assert_denied(self.response())

    def test_counts_are_deterministic_and_same_sql_snapshot(self):
        first = self.response()
        second = self.response()
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body, second.body)
        for connection in self.db.connections:
            self.assertEqual(len(connection.statements), 9)
            self.assertTrue(connection.read_only)

    def test_bounds_and_malformed_stores_fail_closed(self):
        self.db.tables["users"] *= database.MAX_ACCOUNT_ROWS + 1
        self.assert_denied(self.response(), 503)
        self.setUp()
        self.kv.values[team._pending_index_key(fixtures.WORKSPACE_ID)] = json.dumps(["tinv_one"] * 2)
        self.assert_denied(self.response(), 503)
        self.setUp()
        self.kv.values[team._pending_index_key(fixtures.WORKSPACE_ID)] = "DO_NOT_RETURN_KV_SECRET"
        self.assert_denied(self.response(), 503)
        self.setUp()
        self.kv = Mock(side_effect=RuntimeError("DO_NOT_RETURN_KV_SECRET"))
        self.assert_denied(self.response(), 503)

    def test_stored_provenance_is_projected_not_echoed(self):
        self.db.tables["verified_emails"] = [fixtures._email_segment(verification_source="DO_NOT_RETURN_SOURCE_SECRET")]
        response = self.response()
        self.assertEqual(response.status, 200)
        self.assertNotIn(b"DO_NOT_RETURN_SOURCE_SECRET", response.body)
        self.assertEqual(json.loads(response.body)["owner"]["verificationProvenance"], "other-stored-verification-source")

    def test_existing_invitee_cannot_be_promoted_through_diagnostic(self):
        self.db.tables["verified_emails"] = [fixtures._email_segment(verification_source="team-invite-oidc:v1:" + "f" * 64)]
        self.assert_denied(self.response(), 403)

    def test_subject_derivation_matches_canonical_invitee_helper(self):
        request = invitee.InviteePreparationRequest(ISSUER, SUBJECT, fixtures.EMAIL, fixtures.WORKSPACE_ID,
            "tinv_synthetic", "f" * 64, fixtures.USER_ID)
        user_id, email_id, identity_id = invitee.derive_invitee_record_ids(request)
        snapshot = database.PostgreSQLIdentityInventoryDiagnostic(self.db).read(self.record)
        owner = snapshot.authority
        expected = SimpleNamespace(
            user=replace(owner.user, user_id=user_id, primary_verified_email_id=email_id),
            primary_verified_email=replace(owner.primary_verified_email, user_id=user_id, email_id=email_id),
            authentication_identity=replace(owner.authentication_identity, user_id=user_id,
                identity_id=identity_id, verified_email_id=email_id))
        self.assertTrue(all(diagnostic._subject_ids(expected).values()))

    def _invitation(self, invitation_id="tinv_pending", email="invited@example.test", expired=False):
        expires_at = (NOW + (-1 if expired else 1000)) * 1000
        created_at = expires_at - team.TEAM_INVITE_TTL_MS
        return {"v": 2, "id": invitation_id, "workspaceId": fixtures.WORKSPACE_ID,
            "inviteeEmail": email, "inviteeName": "Invited", "displayName": "Invited",
            "accessLevel": "Limited", "status": "invited", "createdAt": created_at,
            "updatedAt": created_at, "expiresAt": expires_at,
            "createdByUserId": fixtures.USER_ID, "createdByUserName": "Owner", "tokenDigest": "f" * 64}

    def _put_invitation(self, invitation):
        wire = team._canonical_json(invitation)
        for key in (team._workspace_invitation_key(invitation["workspaceId"], invitation["id"]),
                    team._workspace_recipient_invitation_key(invitation["workspaceId"], invitation["inviteeEmail"]),
                    team._invitation_token_key(invitation["id"], invitation["tokenDigest"])):
            self.kv.values[key] = wire

    def test_pending_counts_ignore_expiry_without_pruning(self):
        live, expired = self._invitation(), self._invitation("tinv_expired", "expired@example.test", True)
        self._put_invitation(live)
        self._put_invitation(expired)
        self.kv.values[team._pending_index_key(fixtures.WORKSPACE_ID)] = json.dumps([live["id"], expired["id"]])
        before = deepcopy(self.kv.values)
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(json.loads(response.body)["counts"]["pendingTeamInvitations"], 1)
        self.assertEqual(self.kv.values, before)
        self.assertNotIn(b"f" * 64, response.body)
        self.assertNotIn(b"invited@example.test", response.body)

    def test_missing_or_inconsistent_invitation_copy_fails_closed(self):
        live = self._invitation()
        self._put_invitation(live)
        self.kv.values[team._pending_index_key(fixtures.WORKSPACE_ID)] = json.dumps([live["id"]])
        self.kv.values.pop(team._invitation_token_key(live["id"], live["tokenDigest"]))
        self.assert_denied(self.response(), 503)

    def test_suspended_member_is_counted_and_not_activated(self):
        self.db.tables["users"].append(fixtures._user_segment(user_id=fixtures.OTHER_USER_ID, primary_email_id=fixtures.OTHER_EMAIL_ID))
        self.db.tables["verified_emails"].append(fixtures._email_segment(email_id=fixtures.OTHER_EMAIL_ID,
            user_id=fixtures.OTHER_USER_ID, canonical_email="invited@example.test",
            verification_source="team-invite-oidc:v1:" + "f" * 64))
        self.db.tables["workspace_memberships"].append(fixtures._membership_segment(user_id=fixtures.OTHER_USER_ID, status="suspended"))
        before = deepcopy(self.db.tables)
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(json.loads(response.body)["counts"]["suspendedOrIncompleteMemberProvisioning"], 1)
        self.assertEqual(self.db.tables, before)

    def test_completed_invitee_provenance_is_verified_and_missing_pointer_is_incomplete(self):
        invitation = self._invitation("tinv_accepted")
        request = invitee.InviteePreparationRequest(ISSUER, "email|invitee", invitation["inviteeEmail"],
            fixtures.WORKSPACE_ID, invitation["id"], invitation["tokenDigest"], fixtures.USER_ID)
        prepared_rows = invitee._expected_rows(request, NOW - 500)
        user_id, email_id, identity_id = invitee.derive_invitee_record_ids(request)
        for table, row in prepared_rows.items():
            if table == "workspace_memberships":
                row = (*row[:4], "active", row[5], fixtures._dt(NOW - 100), 2)
            self.db.tables[table].append(row)
        invitation.update(status="accepted", acceptedAt=(NOW - 100) * 1000,
            updatedAt=(NOW - 100) * 1000, acceptedByUserId=user_id, acceptedByEmail=invitation["inviteeEmail"])
        self._put_invitation(invitation)
        from api.auth.runtime import AuthenticatedMemberContext
        member = team.build_membership_record(invitation_record=invitation,
            recipient=AuthenticatedMemberContext(user_id, invitation["inviteeEmail"], "Invited", fixtures.WORKSPACE_ID, "member"),
            accepted_at=(NOW - 100) * 1000)
        self.kv.values[team._member_key(fixtures.WORKSPACE_ID, invitation["inviteeEmail"])] = team._canonical_json(member)
        pointer_key = team._member_user_pointer_key(fixtures.WORKSPACE_ID, user_id)
        self.kv.values[pointer_key] = team._canonical_json(team._build_member_user_pointer(member))
        self.kv.values[team._members_index_key(fixtures.WORKSPACE_ID)] = json.dumps([invitation["inviteeEmail"]])
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(json.loads(response.body)["counts"]["suspendedOrIncompleteMemberProvisioning"], 0)
        self.kv.values.pop(pointer_key)
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        self.assertEqual(json.loads(response.body)["counts"]["suspendedOrIncompleteMemberProvisioning"], 1)

    def test_allowlist_matches_are_booleans_without_env_values_or_digests(self):
        key = b"c" * 32
        owner_digest = security.derive_owner_allowlist_entry(key, ISSUER, 1, SUBJECT)
        mailbox_digest = security.derive_mailbox_allowlist_entry(key, ISSUER, 1, SUBJECT, "mailbox-one")
        environment = {**ENV, "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": base64.urlsafe_b64encode(key).rstrip(b"=").decode(),
            "CUEVION_COLLAB_V2_OWNER_ALLOWLIST": owner_digest, "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": mailbox_digest}
        self.kv.values[CONFIG_KEY] = json.dumps({"email": fixtures.EMAIL, "mailboxIds": ["mailbox-one"],
                                               "mailboxCount": 1, "fingerprint": "internal-only"})
        response = self.response(environment=environment)
        self.assertEqual(response.status, 200, response.body)
        payload = json.loads(response.body)
        self.assertTrue(payload["owner"]["collaborationIssuerSubjectBindingsExist"])
        self.assertEqual(payload["subjectDependentState"]["collaborationRolloutBindings"]["matchingMailboxBindings"], 1)
        for value in (*environment.values(), self.cookie, self.record.session_id,
                      self.record.binding_digest, "internal-only", "mailbox-one"):
            self.assertNotIn(value.encode(), response.body)
        self.assertFalse(any(name.lower() == "set-cookie" for name, _ in response.headers))

    def test_partial_allowlist_configuration_fails_closed(self):
        self.assert_denied(self.response(environment={**ENV, "CUEVION_COLLAB_V2_HTTP_MODE": "owner_read"}), 503)
        self.assert_denied(self.response(environment={**ENV, "CUEVION_COLLAB_V2_OWNER_ALLOWLIST": "invalid"}), 503)

    def test_http_adapter_uses_exact_route_and_never_logs(self):
        module = importlib.import_module("api.auth.identity-inventory-diagnostic")
        for method in ("GET", "POST", "HEAD"):
            handler = AdapterHandler(method, diagnostic.ROUTE, self.headers)
            with patch.object(diagnostic, "diagnostic_response", return_value=self.response(method=method)) as respond:
                module.handler._respond(handler)
                respond.assert_called_once_with(method, self.headers, diagnostic.ROUTE)
            self.assertIn(("Cache-Control", "no-store"), handler.response_headers)
            if method == "HEAD":
                self.assertEqual(handler.wfile.getvalue(), b"")
        with patch("sys.stderr") as stderr:
            module.handler.log_message(None, "%s", "DO_NOT_LOG")
            stderr.write.assert_not_called()
        handler = AdapterHandler("UNKNOWN", diagnostic.ROUTE, self.headers)
        module.handler.send_error(handler, 501, "DO_NOT_ECHO_REQUEST")
        self.assertEqual(handler.status, 405)
        self.assertIn(("Cache-Control", "no-store"), handler.response_headers)
        self.assertNotIn(b"DO_NOT_ECHO_REQUEST", handler.wfile.getvalue())


class RedisDiagnosticTests(unittest.TestCase):
    """Run the same route against disposable Redis, with scripted PostgreSQL."""

    setUp = DiagnosticTests.setUp
    response = DiagnosticTests.response
    assert_denied = DiagnosticTests.assert_denied

    @classmethod
    def setUpClass(cls):
        if shutil.which("redis-server") is None:
            raise unittest.SkipTest("redis-server required for disposable Redis verification")
        cls.temp = tempfile.TemporaryDirectory(prefix="cuevion-identity-", dir="/tmp")
        cls.socket_path = cls.temp.name + "/redis.sock"
        cls.process = subprocess.Popen(["redis-server", "--port", "0", "--save", "", "--appendonly", "no",
            "--unixsocket", cls.socket_path, "--unixsocketperm", "700"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if Path(cls.socket_path).exists():
                break
            time.sleep(0.02)
        else:
            cls.process.terminate()
            cls.process.wait()
            cls.temp.cleanup()
            raise RuntimeError("disposable Redis unavailable")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.temp.cleanup()

    def redis(self, *command):
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(self.socket_path)
            parts = [str(value).encode() for value in command]
            client.sendall(b"*" + str(len(parts)).encode() + b"\r\n" + b"".join(
                b"$" + str(len(part)).encode() + b"\r\n" + part + b"\r\n" for part in parts))
            with client.makefile("rb") as stream:
                def read():
                    line = stream.readline()
                    kind, value = line[:1], line[1:-2]
                    if kind == b"-":
                        raise RuntimeError("disposable Redis command rejected")
                    if kind == b"+":
                        return value.decode()
                    if kind == b":":
                        return int(value)
                    if kind == b"$":
                        length = int(value)
                        if length == -1:
                            return None
                        result = stream.read(length).decode()
                        stream.read(2)
                        return result
                    if kind == b"*":
                        return [read() for _ in range(int(value))]
                    raise RuntimeError("invalid disposable Redis response")
                return read()

    def test_real_lua_zero_data_and_ttl_changes_and_config_projection(self):
        self.redis("FLUSHDB")  # Disposable Unix-socket test store only.
        for key, raw in self.kv.values.items():
            self.redis("SET", key, raw, "EX", 3600)
        config = {"email": fixtures.EMAIL, "managedInboxes": [{"id": "mailbox-one", "refreshToken": "DO_NOT_RETURN_OAUTH"}],
                  "password": "DO_NOT_RETURN_PASSWORD", "messages": "DO_NOT_RETURN_MESSAGES" * 3000}
        self.redis("SET", CONFIG_KEY, json.dumps(config))
        before = {key: self.redis("GET", key) for key in self.redis("KEYS", "*")}
        ttl_before = self.redis("PTTL", self.session_key)
        transport_outputs = []
        def transport(command):
            self.assertEqual(command[:2], ["EVAL", diagnostic._SNAPSHOT_LUA])
            result = self.redis(*command)
            transport_outputs.append(result)
            return {"result": result}
        self.kv = transport
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        after = {key: self.redis("GET", key) for key in self.redis("KEYS", "*")}
        self.assertEqual(before, after)
        ttl_after = self.redis("PTTL", self.session_key)
        self.assertTrue(0 <= ttl_before - ttl_after < 2000)
        for secret in ("DO_NOT_RETURN_OAUTH", "DO_NOT_RETURN_PASSWORD", "DO_NOT_RETURN_MESSAGES"):
            self.assertNotIn(secret, json.dumps(transport_outputs))
            self.assertNotIn(secret.encode(), response.body)
        self.assertEqual(json.loads(response.body)["subjectDependentState"]["managedMailboxCount"], 1)

    def test_real_lua_oversized_and_wrong_type_records_fail_without_mutation(self):
        for raw in ("x" * 16385, None):
            self.redis("FLUSHDB")
            if raw is None:
                self.redis("LPUSH", self.session_key, "invalid-type")
            else:
                self.redis("SET", self.session_key, raw)
            self.kv = lambda command: {"result": self.redis(*command)}
            self.assert_denied(self.response(), 503)
            self.assertEqual(self.redis("DBSIZE"), 1)


if __name__ == "__main__":
    unittest.main()
