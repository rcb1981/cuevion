"""Phase 1 boundary, dual-proof callback, continuity and configuration tests."""

from dataclasses import replace
import hashlib
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit, urlencode

from api.auth import auth0_flow as flow, http, models, runtime, session_store
from api.auth.test_auth_routes import MemoryCommands, AdapterHandler
from api.auth import test_auth0_flow as tokens
from api.collaboration import owner_request_security as security
from cuevion_auth import current_account_repository_contract as contract
from cuevion_migration import owner_passkey as migration
from cuevion_db import identity_inventory_diagnostic as inventory
from cuevion_db import postgresql_current_account_repository as canonical
from tests.cuevion_db import test_postgresql_current_account_repository as rows


NOW = tokens.NOW
SUBJECT = "email|original-owner"
NEW_SUBJECT = "auth0|new-database-owner"
ENV = {"VERCEL_ENV": "production", "CUEVION_AUTH0_DOMAIN": flow.AUTH0_DOMAIN,
       "CUEVION_AUTH0_CLIENT_ID": tokens.CLIENT_ID,
       "CUEVION_AUTH0_CLIENT_SECRET": tokens.CLIENT_SECRET,
       "CUEVION_AUTH_SESSION_SECRET": tokens.SESSION_SECRET}


def snapshot():
    user = canonical._decode_user(rows._user_segment())
    email = canonical._decode_verified_email(rows._email_segment(
        verification_source="cuevion_first_account_operator_v1"))
    identity = canonical._decode_authentication_identity(rows._identity_segment(
        issuer=flow.AUTH0_ISSUER, subject=SUBJECT, method="email_otp"))
    workspace = canonical._decode_workspace(rows._workspace_segment())
    member = canonical._decode_workspace_membership(rows._membership_segment(role="owner"))
    authority = contract.CurrentAccountAuthority(user, email, identity, workspace, member)
    return inventory.AccountSnapshot(authority, (user,), (email,), (identity,), (workspace,), (member,))


class Memory(MemoryCommands):
    def __call__(self, command):
        if command[0] == "EVAL":
            self.commands.append(list(command))
            assert command[1] == migration.diagnostic._SNAPSHOT_LUA
            return {"result": [self.values.get(k) for k in command[3:3 + command[2]]]}
        return super().__call__(command)


class Reader:
    def __init__(self, value):
        self.snapshot = value
        self.calls = []

    def read(self, session):
        self.calls.append(session)
        value = self.snapshot
        identity = next(i for i in value.identities if i.issuer == session.issuer and i.subject == session.subject)
        authority = contract.CurrentAccountAuthority(value.users[0], value.emails[0], identity,
                                                       value.workspaces[0], value.memberships[0])
        return replace(value, authority=authority)


class Repository:
    def __init__(self, reader):
        self.reader = reader
        self.calls = []

    def attach(self, value, session, identity, *, now, revalidate):
        revalidate()
        self.calls.append((value, session, identity))
        new = models.AuthenticationIdentity(1, "aid_" + rows._b64(9), value.users[0].user_id,
            identity.issuer, identity.subject, models.AuthenticationMethod.OIDC,
            models.AuthenticationIdentityStatus.ACTIVE, value.emails[0].email_id, now, None, 1)
        self.reader.snapshot = replace(value, identities=tuple(sorted(value.identities + (new,), key=lambda i: i.identity_id)))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = dict(ENV)
        self.memory = Memory()
        self.store = session_store.AuthSessionStore(self.memory)
        self.session, cookie = session_store.create_server_session(self.store,
            secret=tokens.SESSION_SECRET, user_id=rows.USER_ID, workspace_id=rows.WORKSPACE_ID,
            security_epoch=3, workspace_role="owner", issuer=flow.AUTH0_ISSUER, subject=SUBJECT, now=NOW - 100)
        self.cookie = cookie.split(";", 1)[0]
        self.session_key = next(iter(self.memory.values))
        self.headers = (("Host", "app.cuevion.com"), ("Origin", http.CANONICAL_APP_ORIGIN),
                        ("Sec-Fetch-Site", "same-origin"), ("Cookie", self.cookie))
        self.reader = Reader(snapshot())
        self.repository = Repository(self.reader)

    def response(self, operation="start", **overrides):
        args = dict(method="POST" if operation == "start" else "GET", raw_headers=self.headers,
                    path=migration.START_ROUTE if operation == "start" else migration.STATUS_ROUTE,
                    environment=self.environment, now=NOW, operation=operation,
                    reader_factory=lambda _: self.reader, transport_factory=lambda _: self.memory)
        args.update(overrides)
        return migration.migration_response(**args)

    def start(self):
        response = self.response()
        self.assertEqual(response.status, 200, response.body)
        self.assertIn(("Content-Length", str(len(response.body))), response.headers)
        self.transaction_cookie = next(v.split(";", 1)[0] for k, v in response.headers if k == "Set-Cookie")
        self.transaction = flow.decrypt_transaction_cookie(self.transaction_cookie.split("=", 1)[1],
            flow.parse_auth0_configuration(self.environment), NOW)
        self.url = json.loads(response.body)["authorizationUrl"]
        return response

    def callback(self, *, overrides=None, now=NOW + 1, state=None, cookie=None, dependencies=None):
        claims = tokens._claims(email=rows.EMAIL, sub=NEW_SUBJECT, nonce=self.transaction.nonce)
        claims.update(overrides or {})
        def exchange(request):
            body = parse_qs(request.body.decode())
            self.assertEqual(body["code_verifier"], [self.transaction.code_verifier])
            return flow.OutboundResponse(200, flow.AUTH0_TOKEN_ENDPOINT, (("Content-Type", "application/json"),),
                json.dumps({"id_token": tokens._token(claims)}).encode())
        headers = (("Host", "app.cuevion.com"),
                   ("Cookie", (cookie or self.cookie) + "; " + self.transaction_cookie))
        path = "/api/auth/callback?" + urlencode({"code": "synthetic-code", "state": state or self.transaction.state})
        deps = dict(reader_factory=lambda _: self.reader, transport_factory=lambda _: self.memory,
                    repository_factory=lambda _: self.repository)
        deps.update(dependencies or {})
        return runtime.callback_response("GET", headers, path, environment=self.environment, now=now,
            token_transport=exchange,
            jwks_transport=lambda _: flow.OutboundResponse(200, flow.AUTH0_JWKS_ENDPOINT, (("Content-Type", "application/json"),), tokens._jwks()),
            session_store_factory=lambda _: self.store,
            authority_factory=lambda _: self.fail("migration entered ordinary login"),
            migration_dependencies=deps)

    def denied_callback(self, **kwargs):
        before = self.memory.values[self.session_key]
        response = self.callback(**kwargs)
        self.assertIn(("Location", "/login?error=authentication_failed"), response.headers)
        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.memory.values.get(self.session_key), before)
        self.assertFalse(any(k == "Set-Cookie" and v.startswith(session_store.SESSION_COOKIE_NAME + "=")
                             for k, v in response.headers))

    def test_unauthenticated_denied_before_access(self):
        self.assertEqual(self.response(raw_headers=self.headers[:-1]).status, 401)
        self.assertEqual(self.reader.calls, [])

    def test_member_admin_denied(self):
        for role in ("member", "admin"):
            with self.subTest(role=role):
                self.memory.values[self.session_key] = session_store._encode_record(replace(self.session, workspace_role=role))
                self.assertEqual(self.response().status, 403)
        self.assertEqual(self.reader.calls, [])

    def test_non_initial_owner_and_invite_provenance_denied(self):
        for field, record in (
            ("workspaces", replace(snapshot().workspaces[0], created_by_user_id=rows.OTHER_USER_ID)),
            ("emails", replace(snapshot().emails[0], verification_source="team-invite-oidc:v1:" + "a" * 64)),
            ("memberships", replace(snapshot().memberships[0], role=models.WorkspaceRole.ADMIN)),
        ):
            with self.subTest(field=field):
                self.reader.snapshot = replace(snapshot(), **{field: (record,)})
                self.assertNotEqual(self.response().status, 200)

    def test_cross_origin_duplicate_origin_host_and_methods_denied(self):
        for headers in (self.headers + (("Origin", http.CANONICAL_APP_ORIGIN),),
                        tuple((k, "https://attacker.test" if k == "Origin" else v) for k,v in self.headers),
                        tuple((k, "cross-site" if k == "Sec-Fetch-Site" else v) for k,v in self.headers),
                        tuple((k, "attacker.test" if k == "Host" else v) for k,v in self.headers)):
            self.assertNotEqual(self.response(raw_headers=headers).status, 200)
        for method in ("GET", "PUT", "HEAD", "OPTIONS", "post", "POST ", None):
            self.assertNotEqual(self.response(method=method).status, 200)
        self.assertEqual(self.reader.calls, [])

    def test_production_only_and_body_query_selectors_denied(self):
        for environment in ({**ENV, "VERCEL_ENV": "preview"}, {**ENV, "VERCEL_ENV": "development"}, {}):
            self.assertEqual(self.response(environment=environment).status, 404)
        for path in (migration.START_ROUTE + "?connection=email", migration.START_ROUTE + "/"):
            self.assertEqual(self.response(path=path).status, 400)
        for header in (("Content-Length", "1"), ("Transfer-Encoding", "chunked")):
            self.assertEqual(self.response(raw_headers=self.headers + (header,)).status, 400)

    def test_envelope_rejects_unexpected_rows_disabled_identity_and_epoch(self):
        cases = [replace(snapshot(), users=snapshot().users * 2),
                 replace(snapshot(), identities=snapshot().identities * 2),
                 replace(snapshot(), users=(replace(snapshot().users[0], security_epoch=4),)),
                 replace(snapshot(), identities=(replace(snapshot().identities[0], status=models.AuthenticationIdentityStatus.DISABLED),))]
        for value in cases:
            self.reader.snapshot = value
            self.assertNotEqual(self.response().status, 200)

    def test_pending_team_invite_or_continuation_denied(self):
        base = {"pendingTeamInvitations": 0, "suspendedOrIncompleteMemberProvisioning": 0,
                "ownerRecipientInvitationPresent": False, "currentSessionInviteContinuationPresent": False}
        for key in base:
            with patch.object(migration.diagnostic, "_read_team_and_config", return_value=({**base, key: 1}, [])):
                self.assertEqual(self.response().status, 403)

    def test_start_binding_cookie_and_pkce(self):
        self.start()
        query = parse_qs(urlsplit(self.url).query)
        self.assertEqual(query["connection"], [flow.DATABASE_CONNECTION])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], [tokens._b64(hashlib.sha256(self.transaction.code_verifier.encode()).digest())])
        raw = self.memory.values[migration._key(tokens.SESSION_SECRET, "candidate", self.transaction.owner_migration_id)]
        binding = json.loads(raw)["binding"]
        self.assertEqual(binding["session"]["session_id"], self.session.session_id)
        for key, value in (("userId", rows.USER_ID), ("workspaceId", rows.WORKSPACE_ID),
                           ("securityEpoch", 3), ("issuer", flow.AUTH0_ISSUER), ("subject", SUBJECT), ("email", rows.EMAIL)):
            self.assertEqual(binding[key], value)
        self.assertEqual(binding["purpose"], migration.PURPOSE)
        self.assertNotIn(self.transaction.code_verifier, raw)
        self.assertEqual(self.transaction.expires_at - self.transaction.issued_at, 600)
        self.assertTrue(any(c[0] == "SET" and c[-3:] == ["EX", 600, "NX"] for c in self.memory.commands))

    def test_success_adds_identity_preserves_session_and_normal_authority(self):
        self.start()
        before = self.memory.values[self.session_key]
        response = self.callback()
        self.assertIn(("Location", "/?passkey_migration=identity_added"), response.headers)
        self.assertEqual(len(self.repository.calls), 1)
        self.assertEqual(self.memory.values[self.session_key], before)
        self.assertFalse(any(c[0] == "DEL" for c in self.memory.commands))
        self.assertEqual(self.reader.snapshot.identities[0], snapshot().identities[0])
        self.assertEqual(self.reader.snapshot.users, snapshot().users)

    def test_expired_state_mismatch_and_replay_fail(self):
        for kwargs in ({"now": NOW + 600}, {"state": tokens._b64(b"X" * 32)}):
            self.setUp()
            self.start()
            self.denied_callback(**kwargs)
        self.setUp()
        self.start()
        self.callback()
        self.repository.calls.clear()
        self.denied_callback()

    def test_bad_identity_claims_fail_without_canonical_write(self):
        cases = ({"email": "different@example.test"}, {"email_verified": False},
                 {"email_verified": "true"}, {"sub": SUBJECT}, {"iss": "https://foreign.example/"},
                 {"sub": "google-oauth2|other"}, {"sub": "auth0|"}, {"nonce": tokens._b64(b"X" * 32)},
                 {"aud": "wrong-client"}, {"exp": NOW - 100})
        for claims in cases:
            with self.subTest(claims=claims):
                self.setUp()
                self.start()
                self.denied_callback(overrides=claims)

    def test_changed_session_binding_or_graph_or_missing_candidate_denied(self):
        for change in ("epoch", "email", "row_version", "candidate", "candidate_mac", "other_session", "session_revoked"):
            with self.subTest(change=change):
                self.setUp()
                self.start()
                if change == "epoch":
                    self.reader.snapshot = replace(snapshot(), users=(replace(snapshot().users[0], security_epoch=4),))
                elif change == "email":
                    self.reader.snapshot = replace(snapshot(), emails=(replace(snapshot().emails[0], canonical_email="changed@example.test"),))
                elif change == "row_version":
                    self.reader.snapshot = replace(snapshot(), identities=(replace(snapshot().identities[0], row_version=6),))
                elif change in ("candidate", "candidate_mac"):
                    key = migration._key(tokens.SESSION_SECRET, "candidate", self.transaction.owner_migration_id)
                    self.memory.values[key] = "{}" if change == "candidate_mac" else ""
                elif change == "other_session":
                    _, cookie = session_store.create_server_session(self.store, secret=tokens.SESSION_SECRET,
                        user_id=rows.USER_ID, workspace_id=rows.WORKSPACE_ID, security_epoch=3,
                        issuer=flow.AUTH0_ISSUER, subject=SUBJECT, now=NOW)
                    self.denied_callback(cookie=cookie.split(";", 1)[0])
                    continue
                else:
                    self.memory.values[self.session_key] = "invalid"
                self.denied_callback()

    def test_no_remaining_session_lifetime_denied(self):
        self.memory.values[self.session_key] = session_store._encode_record(replace(self.session, expires_at=NOW + 599))
        self.assertEqual(self.response().status, 403)

    def test_bound_target_same_or_other_user_is_rejected(self):
        for user_id in (rows.USER_ID, rows.OTHER_USER_ID):
            self.setUp()
            self.start()
            extra = replace(snapshot().identities[0], identity_id="aid_" + rows._b64(9),
                            subject=NEW_SUBJECT, method=models.AuthenticationMethod.OIDC, user_id=user_id)
            self.reader.snapshot = replace(snapshot(), identities=snapshot().identities + (extra,))
            self.denied_callback()

    def test_status_pending_expired_and_no_sensitive_fields(self):
        self.assertEqual(json.loads(self.response("status").body)["candidate"], "absent")
        self.start()
        self.assertEqual(json.loads(self.response("status").body)["candidate"], "pending")
        response = self.response("status", now=NOW + 600)
        self.assertEqual(json.loads(response.body)["candidate"], "expired")
        for secret in (self.session.session_id, self.cookie, self.transaction_cookie,
                       tokens.SESSION_SECRET, tokens.CLIENT_SECRET, self.transaction.code_verifier):
            self.assertNotIn(secret.encode(), response.body)
        self.assertIn(("Cache-Control", "no-store"), response.headers)

    def test_failed_consumed_callback_status_requires_fresh_start(self):
        self.start()
        self.denied_callback(overrides={"email_verified": False})
        self.assertEqual(json.loads(self.response("status").body)["candidate"], "consumed")

    def test_lost_candidate_is_not_reported_pending(self):
        self.start()
        key = migration._key(tokens.SESSION_SECRET, "candidate", self.transaction.owner_migration_id)
        self.memory.values.pop(key)
        self.assertEqual(json.loads(self.response("status").body)["candidate"], "absent")

    def test_status_accepts_browser_get_without_origin(self):
        headers = tuple((k, v) for k, v in self.headers if k != "Origin")
        self.assertEqual(self.response("status", raw_headers=headers).status, 200)
        self.assertEqual(self.response(raw_headers=headers).status, 403)

    def test_preview_callback_cannot_attach_or_rotate(self):
        self.start()
        self.environment["VERCEL_ENV"] = "preview"
        self.denied_callback()

    def test_revocation_during_callback_work_is_rechecked_without_session_delete(self):
        self.start()
        class RevokeBeforeInsert:
            def attach(inner, *args, revalidate, **kwargs):
                self.memory.values.pop(self.session_key)
                revalidate()
                self.fail("revoked session reached write")
        response = self.callback(dependencies={"repository_factory": lambda _: RevokeBeforeInsert()})
        self.assertIn(("Location", "/login?error=authentication_failed"), response.headers)
        self.assertFalse(any(c[0] == "DEL" for c in self.memory.commands))

    def collaboration(self):
        key = b"K" * 32
        old = snapshot().identities[0]
        self.environment.update({"CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY": tokens._b64(key),
            "CUEVION_COLLAB_V2_OWNER_ALLOWLIST": security.derive_owner_allowlist_entry(key, old.issuer, 1, old.subject),
            "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST": security.derive_mailbox_allowlist_entry(key, old.issuer, 1, old.subject, "authorized-mailbox")})
        self.memory.values["cuevion:user:v1:" + rows.EMAIL] = json.dumps({"email": rows.EMAIL,
            "mailboxIds": ["authorized-mailbox", "unauthorized-mailbox"], "mailboxCount": 2, "fingerprint": "opaque"})
        return key

    def test_collaboration_only_adds_existing_authorized_bindings_and_status_proves_new(self):
        key = self.collaboration()
        before_env = dict(self.environment)
        self.start()
        self.callback()
        response = self.response("status")
        self.assertEqual(response.status, 200, response.body)
        result = json.loads(response.body)
        self.assertTrue(result["newIdentityResolvesSameUser"])
        self.assertTrue(any(s.subject == NEW_SUBJECT for s in self.reader.calls))
        additions = result["collaboration"]
        self.assertEqual(additions["ownerEntriesToAdd"], [security.derive_owner_allowlist_entry(key, flow.AUTH0_ISSUER, 1, NEW_SUBJECT)])
        self.assertEqual(additions["mailboxEntriesToAdd"], [security.derive_mailbox_allowlist_entry(key, flow.AUTH0_ISSUER, 1, NEW_SUBJECT, "authorized-mailbox")])
        self.assertEqual(additions["authorizedMailboxBindings"], 1)
        self.assertEqual(self.environment, before_env)
        for value in (tokens._b64(key), "authorized-mailbox", "unauthorized-mailbox"):
            self.assertNotIn(value.encode(), response.body)
        self.environment["CUEVION_COLLAB_V2_OWNER_ALLOWLIST"] += "," + additions["ownerEntriesToAdd"][0]
        self.environment["CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST"] += "," + additions["mailboxEntriesToAdd"][0]
        result = json.loads(self.response("status").body)
        self.assertTrue(result["collaboration"]["allRequiredEntriesPresent"])
        self.assertEqual(result["phase"], "ready_for_normal_login_cutover")

    def test_status_owner_and_same_origin_boundaries(self):
        self.assertEqual(self.response("status", raw_headers=self.headers[:-1]).status, 401)
        self.assertEqual(self.response("status", method="POST").status, 405)
        self.assertEqual(self.response("status", path=migration.STATUS_ROUTE + "?user=other").status, 400)

    def test_database_identity_owner_can_read_status_but_cannot_start_again(self):
        self.collaboration()
        self.start()
        self.callback()
        _, cookie = session_store.create_server_session(self.store, secret=tokens.SESSION_SECRET,
            user_id=rows.USER_ID, workspace_id=rows.WORKSPACE_ID, security_epoch=3,
            issuer=flow.AUTH0_ISSUER, subject=NEW_SUBJECT, now=NOW + 2)
        headers = self.headers[:-1] + (("Cookie", cookie.split(";", 1)[0]),)
        response = self.response("status", raw_headers=headers, now=NOW + 3)
        self.assertEqual(response.status, 200, response.body)
        self.assertTrue(json.loads(response.body)["newIdentityResolvesSameUser"])
        self.assertEqual(self.response(raw_headers=headers, now=NOW + 3).status, 403)

    def test_route_handlers_unknown_methods_are_generic_and_logs_disabled(self):
        for route in ("start", "status"):
            handler = importlib.import_module("api.auth.passkey-migration." + route).handler
            fake = AdapterHandler("CUSTOM", "/api/auth/passkey-migration/" + route, self.headers)
            handler.send_error(fake, 501, "secret message", "secret explanation")
            self.assertEqual(fake.status, 405)
            self.assertNotIn(b"secret", fake.wfile.getvalue())


class LoginConnectionTests(unittest.TestCase):
    def test_absent_and_two_exact_values(self):
        self.assertEqual(flow.parse_login_connection({}), "email")
        for connection in ("email", flow.DATABASE_CONNECTION):
            config = flow.parse_auth0_configuration({**ENV, "CUEVION_AUTH0_LOGIN_CONNECTION": connection})
            request = flow.build_authorization_request(config, NOW)
            self.assertEqual(parse_qs(urlsplit(request.authorization_url).query)["connection"], [connection])
            migration_request = flow.build_authorization_request(config, NOW, owner_migration_id=tokens._b64(b"M" * 32))
            self.assertEqual(parse_qs(urlsplit(migration_request.authorization_url).query)["connection"], [flow.DATABASE_CONNECTION])

    def test_unknown_malformed_configuration_fails_closed(self):
        for value in (None, "", " email", "email ", "EMAIL", "google-oauth2", "email\n", 1, True):
            with self.subTest(value=value), self.assertRaises(flow.Auth0FlowError):
                flow.parse_auth0_configuration({**ENV, "CUEVION_AUTH0_LOGIN_CONNECTION": value})

    def test_browser_query_cannot_select_connection(self):
        headers = (("Host", "app.cuevion.com"),)
        for value in ("email", flow.DATABASE_CONNECTION):
            response = runtime.login_response("GET", headers, "/api/auth/login?connection=" + value,
                                              environment=ENV, now=NOW)
            self.assertEqual(response.status, 400)

    def test_normal_login_http_route_uses_server_connection(self):
        for connection in ("email", flow.DATABASE_CONNECTION):
            response = runtime.login_response("GET", (("Host", "app.cuevion.com"),),
                "/api/auth/login", environment={**ENV, "CUEVION_AUTH0_LOGIN_CONNECTION": connection}, now=NOW)
            self.assertEqual(response.status, 303)
            url = next(value for key, value in response.headers if key == "Location")
            self.assertEqual(parse_qs(urlsplit(url).query)["connection"], [connection])

    def test_team_invite_uses_configured_normal_connection_and_cannot_mix_migration(self):
        token = "tinv_test." + tokens._b64(b"T" * 32)
        for connection in ("email", flow.DATABASE_CONNECTION):
            config = flow.parse_auth0_configuration({**ENV, "CUEVION_AUTH0_LOGIN_CONNECTION": connection})
            request = flow.build_authorization_request(config, NOW, team_invite_token=token)
            self.assertEqual(parse_qs(urlsplit(request.authorization_url).query)["connection"], [connection])
            decoded = flow.decrypt_transaction_cookie(tokens._cookie_value(request.transaction_cookie), config, NOW)
            self.assertEqual(decoded.team_invite_token, token)
            self.assertIsNone(decoded.owner_migration_id)
            team = SimpleNamespace(read_provisioning_invitation=lambda *args, **kwargs: None)
            response = runtime.login_response("GET", (("Host", "app.cuevion.com"), ("Sec-Fetch-Site", "same-origin")),
                "/api/auth/login?" + urlencode({"team_invite": token}), now=NOW,
                environment={**ENV, "CUEVION_AUTH0_LOGIN_CONNECTION": connection},
                team_authority_factory=lambda _: team)
            self.assertEqual(response.status, 303)
            url = next(value for key, value in response.headers if key == "Location")
            self.assertEqual(parse_qs(urlsplit(url).query)["connection"], [connection])
            with self.assertRaises(flow.Auth0FlowError):
                flow.build_authorization_request(config, NOW, team_invite_token=token,
                                                  owner_migration_id=tokens._b64(b"M" * 32))


class MigrationRedisTests(unittest.TestCase):
    # Reuse the existing disposable Unix-socket Redis harness, never a runtime DSN.
    from tests.cuevion_auth.test_identity_inventory_diagnostic import RedisDiagnosticTests as _Harness
    setUpClass = classmethod(_Harness.setUpClass.__func__)
    tearDownClass = classmethod(_Harness.tearDownClass.__func__)
    redis = _Harness.redis

    def test_atomic_competing_consumption_and_read_only_status(self):
        from concurrent.futures import ThreadPoolExecutor
        self.redis("FLUSHDB")
        store = session_store.AuthSessionStore(lambda command: {"result": self.redis(*command)})
        state = tokens._b64(b"R" * 32)
        self.assertFalse(store.transaction_consumed(state, tokens.SESSION_SECRET))
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda _: store.consume_transaction(state, tokens.SESSION_SECRET, 600), range(8)))
        self.assertEqual(decisions.count(True), 1)
        key = self.redis("KEYS", session_store.TRANSACTION_USE_KEY_PREFIX + "*")[0]
        ttl = self.redis("PTTL", key)
        self.assertTrue(store.transaction_consumed(state, tokens.SESSION_SECRET))
        self.assertLessEqual(self.redis("PTTL", key), ttl)
        self.assertTrue(0 < ttl <= 600000)

    def test_candidate_server_ttl_is_bounded_and_status_has_no_authority(self):
        self.redis("FLUSHDB")
        harness = MigrationTests()
        harness.setUp()
        harness.start()
        store = session_store.AuthSessionStore(lambda command: {"result": self.redis(*command)})
        migration._save_candidate(store, tokens.SESSION_SECRET, harness.transaction, harness.session, snapshot())
        key = migration._key(tokens.SESSION_SECRET, "candidate", harness.transaction.owner_migration_id)
        self.assertTrue(0 < self.redis("TTL", key) <= 600)
        self.redis("EXPIRE", key, 0)
        self.assertIsNone(store._command(["GET", key]))
        self.assertEqual(migration._candidate_status(store, tokens.SESSION_SECRET, harness.session, NOW + 600), "expired")
