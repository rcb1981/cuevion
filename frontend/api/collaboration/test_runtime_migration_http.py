from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from api import user_config_store
from api.auth.runtime import AuthenticatedMemberContext
from . import authorization, discovery_migration, http_adapter, operator_grant
from . import owner_authentication, owner_http, owner_rate_limit, owner_request_security as security
from . import redis_store, test_owner_http as fixtures

OPERATION = "run_discovery_migration_dry_run_page"
COUNTERS = ("examined", "wouldEnroll", "alreadyPresent", "alreadyPlanned", "skippedInvalid",
            "skippedUnauthorized", "unresolvedIdentity", "staleEntitlement", "missing",
            "capacity", "retry", "deferred")
OUTPUT_FIELDS = set(COUNTERS) | {"v", "dryRun", "scanCalls", "hasMore", "done", "status"}


class RuntimeMigrationHttpTests(unittest.TestCase):
    def setUp(self):
        self.environment = {**fixtures._environment(), operator_grant.MODE_ENV: "dry_run_grant"}
        self.configuration = security.parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(self.environment))
        self.context = fixtures._context()
        self.csrf = security.issue_owner_csrf_token(self.context, self.configuration, now=fixtures.NOW)[0]
        self.member = AuthenticatedMemberContext(fixtures.OWNER_USER_ID, fixtures.OWNER_EMAIL,
                                                "Owner Person", fixtures.WORKSPACE_ID, "owner")
        self.mailbox_config = {"email": fixtures.OWNER_EMAIL, "managedInboxes": [
            {"id": fixtures.MAILBOX_ID, "provider": "google", "email": "inbox@example.com",
             "connected": True, "connectionStatus": "connected", "accessToken": "private-mailbox-token"}]}
        self.reader = Mock(side_effect=lambda _: (
            self.member, {"email": self.member.email},
            {"status": "ok", "config": self.mailbox_config, "error": None}))
        self.authenticate = Mock(return_value=fixtures._claims())
        self.limiter = Mock(return_value=owner_rate_limit.OwnerRateLimitDecision("allowed"))
        self.team = Mock(return_value=(None, "not_active"))
        self.command = Mock(return_value={"status": "ok", "result": ["0", []]})
        self.patches = (
            patch.object(owner_http, "resolve_verified_auth0_owner", self.authenticate),
            patch.object(user_config_store, "read_user_config_for_authenticated_member", self.reader),
            patch.object(authorization, "_resolve_current_authenticated_member",
                         side_effect=lambda _: (self.member, None)),
            patch.object(authorization, "_resolve_active_team_member", self.team),
            patch.object(owner_rate_limit, "consume_owner_rate_limit", self.limiter),
            patch.object(redis_store, "_v2_command", self.command),
            patch.dict(os.environ, {
                redis_store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
                redis_store.V2_INDEX_HMAC_PREVIOUS_ENV: "",
                "KV_REST_API_URL": "https://isolated.example", "KV_REST_API_TOKEN": "fixture-only",
            }),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def request(self, **updates):
        payload = {"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID, **updates}
        return fixtures._request(payload, csrf=self.csrf)

    def response(self, request=None, mode="owner_write"):
        return http_adapter.invoke_safely(lambda: owner_http.owner_response(
            request or self.request(), http_mode=mode, environment=self.environment,
            now=fixtures.NOW), allow_method="POST")

    def reset_work(self):
        for item in (self.reader, self.limiter, self.team, self.command):
            item.reset_mock()

    def test_valid_owner_runs_exactly_once_without_grant_or_checkpoint(self):
        output = io.StringIO()
        with patch.object(discovery_migration, "run_runtime_dry_run_page",
                          wraps=discovery_migration.run_runtime_dry_run_page) as run, \
             patch.object(discovery_migration, "run_page") as manual, \
             patch.object(discovery_migration, "run_page_with_operator_grant") as granted, \
             patch.object(operator_grant, "_issue_operator_grant") as issue, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            response = self.response()
        self.assertEqual(response.status, 200)
        data = fixtures._json(response)["data"]
        self.assertEqual(set(data), OUTPUT_FIELDS)
        self.assertEqual(data, {**dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": True,
                               "scanCalls": 1, "hasMore": False, "done": True, "status": "ok"})
        run.assert_called_once_with(unittest.mock.ANY, unittest.mock.ANY,
                                    owner_mailbox_id=fixtures.MAILBOX_ID,
                                    owner_security_configuration=unittest.mock.ANY)
        manual.assert_not_called(); granted.assert_not_called(); issue.assert_not_called()
        self.command.assert_called_once()
        self.assertEqual(self.command.call_args.args[0],
                         ["SCAN", "0", "MATCH", discovery_migration.SCAN_PATTERN, "COUNT", 100])
        self.limiter.assert_called_once_with(unittest.mock.ANY, owner_rate_limit.RATE_LIMIT_MIGRATION_DRY_RUN,
                                             unittest.mock.ANY, operator_user_id=fixtures.OWNER_USER_ID)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(dict(response.headers)["Cache-Control"], "no-store")
        self.assertEqual(dict(response.headers)["X-Content-Type-Options"], "nosniff")

    def test_owner_read_also_allows_this_read_only_operation(self):
        self.assertEqual(self.response(mode="owner_read").status, 200)

    def test_gate_absent_off_unknown_and_other_http_modes_deny(self):
        for value in (None, "off", "runtime_dry_run", "unknown", "dry_run_grant ", True):
            with self.subTest(mode=value):
                self.reset_work()
                if value is None: self.environment.pop(operator_grant.MODE_ENV, None)
                else: self.environment[operator_grant.MODE_ENV] = value
                self.assertEqual(self.response().status, 404)
                self.reader.assert_not_called(); self.limiter.assert_not_called(); self.command.assert_not_called()
        for mode in ("off", "guest_on", "unknown"):
            self.assertEqual(self.response(mode=mode).status, 404)

    def test_missing_invalid_expired_and_revoked_owner_sessions_are_denied(self):
        for reason in ("authentication_required", "authentication_unavailable"):
            self.authenticate.side_effect = security.OwnerSecurityError(reason)
            self.assertIn(self.response().status, (401, 503))
        self.authenticate.side_effect = None
        self.authenticate.return_value = fixtures._claims(expires_at=fixtures.NOW - 1)
        self.assertEqual(self.response().status, 401)
        self.reader.assert_not_called(); self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_genuine_auth_boundary_does_not_accept_guest_cookies(self):
        self.authenticate.side_effect = owner_authentication.resolve_verified_auth0_owner
        request = self.request()
        request.headers.pairs.append(("Cookie", "cuevion_collab_guest=guest-only-fixture"))
        self.assertEqual(self.response(request).status, 401)
        self.command.assert_not_called(); self.limiter.assert_not_called()

    def test_owner_allowlist_origin_and_session_bound_csrf_are_required(self):
        self.environment["CUEVION_COLLAB_V2_OWNER_ALLOWLIST"] = fixtures._entry(
            fixtures._OWNER_DOMAIN, (fixtures.ISSUER, "1", "another-subject"))
        self.assertEqual(self.response().status, 404)
        self.environment.update(fixtures._environment())
        requests = [fixtures._request({"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID}),
                    fixtures._request({"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID}, csrf="bad")]
        other = fixtures._context(session_id=fixtures._b64(b"x" * 32))
        requests.append(fixtures._request({"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID},
                        csrf=security.issue_owner_csrf_token(other, self.configuration, now=fixtures.NOW)[0]))
        wrong_origin = self.request()
        wrong_origin.headers.pairs[0] = ("Origin", "https://other.example")
        requests.append(wrong_origin)
        for request in requests:
            self.assertEqual(self.response(request).status, 403)
        self.reader.assert_not_called(); self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_only_exact_mailbox_routing_body_is_accepted(self):
        extras = {"dryRun": False, "apply": True, "cursor": "0", "scanBudget": "1",
                  "userId": fixtures.OWNER_USER_ID, "workspaceId": fixtures.WORKSPACE_ID,
                  "email": fixtures.OWNER_EMAIL, "provider": "google", "sourceRef": {},
                  "checkpointPath": "/tmp/forbidden", "grant": "forbidden", "mailboxId": fixtures.MAILBOX_ID,
                  "continuation": "opaque", "key": "forbidden"}
        for key, value in extras.items():
            with self.subTest(field=key):
                self.assertEqual(self.response(self.request(**{key: value})).status, 400)
        for value in (None, "", "UPPER", " primary.mailbox", {}, [], True):
            self.assertEqual(self.response(self.request(ownerMailboxId=value)).status, 400)
        self.assertEqual(self.response(fixtures._request({"operation": OPERATION}, csrf=self.csrf)).status, 400)
        body = json.dumps({"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID})
        duplicate = ('{"ownerMailboxId":"other",' + body[1:]).encode()
        headers = [("Origin", fixtures.ORIGIN), ("Content-Type", "application/json"),
                   ("Content-Length", str(len(duplicate))), ("X-Cuevion-CSRF", self.csrf)]
        self.assertEqual(self.response(fixtures._Request(duplicate, headers=headers)).status, 400)
        self.reader.assert_not_called(); self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_mailbox_not_allowlisted_or_not_owned_denied_before_scan(self):
        self.assertEqual(self.response(self.request(ownerMailboxId="other.mailbox")).status, 403)
        self.reader.assert_not_called()
        self.mailbox_config["managedInboxes"] = []
        self.assertEqual(self.response().status, 403)
        self.mailbox_config["managedInboxes"] = [{"id": fixtures.MAILBOX_ID, "provider": "google"}]
        self.mailbox_config["email"] = "another-owner@example.com"
        self.assertEqual(self.response().status, 503)
        self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_both_supported_providers_and_wrong_provider(self):
        self.mailbox_config["managedInboxes"][0]["provider"] = "custom_imap"
        self.assertEqual(self.response().status, 200)
        self.reset_work()
        self.mailbox_config["managedInboxes"][0]["provider"] = "outlook"
        self.assertEqual(self.response().status, 503)
        self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_current_account_workspace_email_name_and_user_must_match(self):
        for field, value in (("workspace_id", fixtures.OTHER_WORKSPACE_ID), ("email", "other@example.com"),
                             ("name", "Other"), ("user_id", "not-canonical")):
            values = dict(user_id=fixtures.OWNER_USER_ID, email=fixtures.OWNER_EMAIL, name="Owner Person",
                          workspace_id=fixtures.WORKSPACE_ID, membership_role="owner")
            values[field] = value
            self.member = AuthenticatedMemberContext(**values)
            with self.subTest(field=field):
                self.assertEqual(self.response().status, 503 if field == "email" else 403)
        self.limiter.assert_not_called(); self.command.assert_not_called()

    def test_authority_failures_remain_safe_without_scan(self):
        self.reader.side_effect = RuntimeError("private-db-url private-email private-secret")
        response = self.response()
        self.assertEqual(response.status, 503)
        self.assertEqual(fixtures._json(response)["error"]["code"], "authority_unavailable")
        self.command.assert_not_called()

    def test_team_not_enrolled_is_allowed_but_unavailable_or_rebound_denied(self):
        self.assertEqual(self.response().status, 200)
        self.team.return_value = ({"memberUserId": self.member.user_id,
                                   "sourceInvitationId": "tinv_current"}, None)
        self.assertEqual(self.response().status, 200)
        for result in ((None, "unavailable"), ({"memberUserId": "other", "sourceInvitationId": "tinv_x"}, None),
                       ({"memberUserId": self.member.user_id, "sourceInvitationId": "bad"}, None)):
            self.reset_work(); self.team.return_value = result
            self.assertEqual(self.response().status, 503)
            self.command.assert_not_called()

    def test_rate_limited_and_unavailable_never_invoke_migration(self):
        with patch.object(discovery_migration, "run_runtime_dry_run_page") as run:
            for decision, status in ((owner_rate_limit.OwnerRateLimitDecision("limited", 60), 429),
                                     (owner_rate_limit.OwnerRateLimitDecision("unavailable"), 503),
                                     ({"status": "allowed"}, 503)):
                self.limiter.return_value = decision
                response = self.response()
                self.assertEqual(response.status, status)
                if status == 429: self.assertEqual(dict(response.headers)["Retry-After"], "60")
            run.assert_not_called()
        self.command.assert_not_called()

    def test_redis_failure_no_retry_and_no_error_or_log_leak(self):
        self.command.return_value = {"status": "unavailable", "error": {"code": "storage_unavailable"}}
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            response = self.response()
        self.assertEqual(response.status, 503)
        self.command.assert_called_once()
        self.assertEqual(captured.getvalue(), "")
        for text in ("private-mailbox-token", fixtures.OWNER_EMAIL, "sourceRef", "cuevion:collab:"):
            self.assertNotIn(text, response.body.decode())

    def test_continuation_is_boolean_only_and_never_advances(self):
        self.command.return_value = {"status": "ok", "result": ["987654321", []]}
        response = self.response()
        self.assertEqual(response.status, 200)
        self.assertTrue(fixtures._json(response)["data"]["hasMore"])
        self.assertFalse(fixtures._json(response)["data"]["done"])
        self.assertNotIn("987654321", response.body.decode())
        self.command.assert_called_once()

    def test_aggregate_projection_rejects_extra_fields_invalid_types_and_raw_errors(self):
        valid = {**dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": True, "scanCalls": 1,
                 "hasMore": False, "done": True, "status": "ok", "error": None}
        unsafe = "private-source-message guest@example.com cuevion:collab:v2: secret"
        mutations = [{"v": True}, {"dryRun": False}, {"scanCalls": 2}, {"examined": True},
                     {"examined": 6}, {"wouldEnroll": 1}, {"hasMore": "false"}, {"done": False},
                     {"status": unsafe}, {"error": {"code": unsafe}}, {"nextCursor": unsafe},
                     {"collaborationId": unsafe}, {"sourceRef": unsafe}, {"email": unsafe},
                     {"messages": [unsafe]}, {"guests": [unsafe]}, {"enrolled": 1}]
        for mutation in mutations:
            with self.subTest(fields=tuple(mutation)), patch.object(
                    discovery_migration, "run_runtime_dry_run_page", return_value={**valid, **mutation}):
                response = self.response()
                self.assertEqual(response.status, 503)
                self.assertNotIn(unsafe, response.body.decode())

    def test_capacity_and_retry_are_aggregate_success_without_retry(self):
        for reason in ("capacity", "retry"):
            result = {**dict.fromkeys(COUNTERS, 0), "examined": 1, reason: 1, "v": 1,
                      "dryRun": True, "scanCalls": 1, "hasMore": True, "done": False,
                      "status": "blocked", "error": {"code": reason}}
            with patch.object(discovery_migration, "run_runtime_dry_run_page", return_value=result) as run:
                response = self.response()
            self.assertEqual(response.status, 200)
            self.assertEqual(set(fixtures._json(response)["data"]), OUTPUT_FIELDS)
            run.assert_called_once()

    def test_startup_summary_and_guest_routes_do_not_import_migration(self):
        script = '''
import sys
from unittest.mock import patch
from api.collaboration import owner, owner_http, guest, guest_http, operator_grant
from api.collaboration import test_owner_http as f
assert 'api.collaboration.discovery_migration' not in sys.modules
ctx=f._context()
cfg=f.parse_owner_security_configuration(owner_http._trusted_security_snapshot(f._environment()))
csrf=f.issue_owner_csrf_token(ctx,cfg,now=f.NOW)[0]
with patch.object(owner_http,'_resolve_context',return_value=ctx), patch.object(owner_http,'_rate_limit_response',return_value=None), patch.object(owner_http.application,'list_v2_summaries_for_verified_owner',return_value={}):
    f._invoke(f._request({'operation':'list_summaries','cursor':None},csrf=csrf))
assert 'api.collaboration.discovery_migration' not in sys.modules
from api.collaboration import test_guest_http as g
request=g._post({'operation':'run_discovery_migration_dry_run_page','ownerMailboxId':f.MAILBOX_ID})
response=g._invoke(request)
assert response.status != 200
assert 'api.collaboration.discovery_migration' not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
