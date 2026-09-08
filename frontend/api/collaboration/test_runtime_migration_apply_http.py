from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from . import discovery_migration, operator_grant, owner_authentication, owner_rate_limit
from . import owner_request_security as security, test_owner_http as fixtures
from . import test_runtime_migration_http as dry


OPERATION = "apply_discovery_migration_page"
CONFIRMATION = "APPLY_HISTORICAL_DISCOVERY"
COUNTERS = ("examined", "enrolled", "alreadyPresent", "skippedInvalid",
            "skippedUnauthorized", "unresolvedIdentity", "staleEntitlement",
            "missing", "capacity", "retry", "deferred")
OUTPUT_FIELDS = set(COUNTERS) | {"v", "dryRun", "scanCalls", "hasMore", "done", "status"}


class RuntimeMigrationApplyHttpTests(unittest.TestCase):
    def setUp(self):
        # Reuse the real owner/CSRF/current-mailbox boundary fixture, without
        # inheriting tests whose operation is deliberately dry-run only.
        dry.RuntimeMigrationHttpTests.setUp(self)
        self.environment[operator_grant.MODE_ENV] = "runtime_apply"

    response = dry.RuntimeMigrationHttpTests.response
    reset_work = dry.RuntimeMigrationHttpTests.reset_work

    def request(self, **updates):
        return fixtures._request({"operation": OPERATION,
                                  "ownerMailboxId": fixtures.MAILBOX_ID,
                                  "confirmation": CONFIRMATION, **updates}, csrf=self.csrf)

    def test_valid_owner_runs_one_apply_without_dry_run_grant_or_checkpoint(self):
        captured = io.StringIO()
        with patch.object(discovery_migration, "run_runtime_apply_page",
                          wraps=discovery_migration.run_runtime_apply_page) as apply, \
             patch.object(discovery_migration, "run_runtime_dry_run_page") as sample, \
             patch.object(discovery_migration, "run_page") as manual, \
             patch.object(discovery_migration, "run_page_with_operator_grant") as granted, \
             patch.object(operator_grant, "_issue_operator_grant") as issue, \
             patch.object(discovery_migration, "_save") as save, \
             contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            response = self.response()
        self.assertEqual(response.status, 200)
        self.assertEqual(fixtures._json(response)["data"], {
            **dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": False, "scanCalls": 1,
            "hasMore": False, "done": True, "status": "ok"})
        apply.assert_called_once_with(unittest.mock.ANY, unittest.mock.ANY,
                                      owner_mailbox_id=fixtures.MAILBOX_ID,
                                      owner_security_configuration=unittest.mock.ANY)
        for unused in (sample, manual, granted, issue, save):
            unused.assert_not_called()
        self.command.assert_called_once()
        self.assertEqual(self.command.call_args.args[0],
                         ["SCAN", "0", "MATCH", discovery_migration.SCAN_PATTERN, "COUNT", 100])
        self.limiter.assert_called_once_with(unittest.mock.ANY, owner_rate_limit.RATE_LIMIT_MIGRATION_APPLY,
                                             unittest.mock.ANY, operator_user_id=fixtures.OWNER_USER_ID)
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(dict(response.headers)["Cache-Control"], "no-store")

    def test_only_exact_apply_mode_and_owner_write_allow_apply(self):
        for value in (None, "off", "dry_run_grant", "runtime_apply ", "RUNTIME_APPLY", "unknown", True, {}):
            with self.subTest(mode=value):
                if value is None:
                    self.environment.pop(operator_grant.MODE_ENV, None)
                else:
                    self.environment[operator_grant.MODE_ENV] = value
                self.assertEqual(self.response().status, 404)
        self.environment[operator_grant.MODE_ENV] = "runtime_apply"
        for mode in ("off", "owner_read", "guest_on", "unknown"):
            self.assertEqual(self.response(mode=mode).status, 404)
        self.reader.assert_not_called()
        self.limiter.assert_not_called()
        self.command.assert_not_called()

    def test_apply_mode_denies_both_grant_and_dry_run_without_work(self):
        with patch.object(discovery_migration, "run_runtime_apply_page") as apply, \
             patch.object(discovery_migration, "run_runtime_dry_run_page") as sample, \
             patch.object(operator_grant, "_issue_operator_grant") as issue:
            for payload in ({"operation": "issue_migration_operator_grant"},
                            {"operation": dry.OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID}):
                self.assertEqual(self.response(fixtures._request(payload, csrf=self.csrf)).status, 404)
            for unused in (apply, sample, issue):
                unused.assert_not_called()
        self.reader.assert_not_called()
        self.limiter.assert_not_called()
        self.command.assert_not_called()

    def test_confirmation_is_exact_required_and_not_normalized(self):
        for value in (None, True, {}, [], "", CONFIRMATION.lower(), " " + CONFIRMATION,
                      CONFIRMATION + " ", CONFIRMATION + "\n"):
            with self.subTest(confirmation=value):
                self.assertEqual(self.response(self.request(confirmation=value)).status, 400)
        self.assertEqual(self.response(fixtures._request({"operation": OPERATION,
                         "ownerMailboxId": fixtures.MAILBOX_ID}, csrf=self.csrf)).status, 400)
        self.reader.assert_not_called()
        self.limiter.assert_not_called()
        self.command.assert_not_called()

    def test_no_execution_identity_or_extra_input_is_accepted(self):
        extras = {"dryRun": False, "apply": True, "cursor": "0", "scanBudget": 1,
                  "pageSize": 3, "userId": fixtures.OWNER_USER_ID, "workspaceId": fixtures.WORKSPACE_ID,
                  "provider": "google", "sourceRef": {}, "key": "private", "checkpoint": "/tmp/no",
                  "participant": {}, "email": fixtures.OWNER_EMAIL, "continuation": "private",
                  "grant": "private", "unknown": True}
        for name, value in extras.items():
            with self.subTest(field=name):
                self.assertEqual(self.response(self.request(**{name: value})).status, 400)
        for value in (None, "", "UPPER", " primary.mailbox", {}, [], True):
            self.assertEqual(self.response(self.request(ownerMailboxId=value)).status, 400)
        body = json.dumps({"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID,
                           "confirmation": CONFIRMATION})
        duplicate = ('{"confirmation":"' + CONFIRMATION + '",' + body[1:]).encode()
        request = fixtures._Request(duplicate, headers=[("Origin", fixtures.ORIGIN),
            ("Content-Type", "application/json"), ("Content-Length", str(len(duplicate))),
            ("X-Cuevion-CSRF", self.csrf)])
        self.assertEqual(self.response(request).status, 400)
        self.reader.assert_not_called()
        self.limiter.assert_not_called()
        self.command.assert_not_called()

    def test_origin_owner_allowlist_and_session_bound_csrf_precede_apply(self):
        payload = {"operation": OPERATION, "ownerMailboxId": fixtures.MAILBOX_ID,
                   "confirmation": CONFIRMATION}
        other_session = fixtures._context(session_id=fixtures._b64(b"x" * 32))
        other_csrf = security.issue_owner_csrf_token(other_session, self.configuration, now=fixtures.NOW)[0]
        for csrf in (None, "invalid", other_csrf):
            self.assertEqual(self.response(fixtures._request(payload, csrf=csrf)).status, 403)
        request = self.request()
        request.headers.pairs[0] = ("Origin", "https://other.example")
        self.assertEqual(self.response(request).status, 403)
        self.environment["CUEVION_COLLAB_V2_OWNER_ALLOWLIST"] = fixtures._entry(
            fixtures._OWNER_DOMAIN, (fixtures.ISSUER, "1", "another-subject"))
        self.assertEqual(self.response().status, 404)
        self.reader.assert_not_called()
        self.limiter.assert_not_called()
        self.command.assert_not_called()

    def test_genuine_auth_boundary_rejects_guest_only_cookie(self):
        self.authenticate.side_effect = owner_authentication.resolve_verified_auth0_owner
        request = self.request()
        request.headers.pairs.append(("Cookie", "cuevion_collab_guest=guest-only-fixture"))
        self.assertEqual(self.response(request).status, 401)
        self.reader.assert_not_called()
        self.command.assert_not_called()

    test_missing_invalid_expired_and_revoked_owner_sessions_are_denied = (
        dry.RuntimeMigrationHttpTests.test_missing_invalid_expired_and_revoked_owner_sessions_are_denied)
    test_mailbox_not_allowlisted_or_not_owned_denied_before_scan = (
        dry.RuntimeMigrationHttpTests.test_mailbox_not_allowlisted_or_not_owned_denied_before_scan)
    test_both_supported_providers_and_wrong_provider = (
        dry.RuntimeMigrationHttpTests.test_both_supported_providers_and_wrong_provider)
    test_current_account_workspace_email_name_and_user_must_match = (
        dry.RuntimeMigrationHttpTests.test_current_account_workspace_email_name_and_user_must_match)
    test_authority_failures_remain_safe_without_scan = (
        dry.RuntimeMigrationHttpTests.test_authority_failures_remain_safe_without_scan)
    test_team_not_enrolled_is_allowed_but_unavailable_or_rebound_denied = (
        dry.RuntimeMigrationHttpTests.test_team_not_enrolled_is_allowed_but_unavailable_or_rebound_denied)

    def test_apply_limited_or_unavailable_does_not_invoke_engine(self):
        with patch.object(discovery_migration, "run_runtime_apply_page") as apply:
            for decision, status in ((owner_rate_limit.OwnerRateLimitDecision("limited", 60), 429),
                                     (owner_rate_limit.OwnerRateLimitDecision("unavailable"), 503),
                                     ({"status": "allowed"}, 503)):
                self.limiter.return_value = decision
                response = self.response()
                self.assertEqual(response.status, status)
                if status == 429:
                    self.assertEqual(dict(response.headers)["Retry-After"], "60")
            apply.assert_not_called()
        self.command.assert_not_called()

    def test_redis_unavailable_or_exception_does_not_retry_or_leak(self):
        self.command.return_value = {"status": "unavailable", "error": {"code": "storage_unavailable"}}
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            response = self.response()
        self.assertEqual(response.status, 503)
        self.assertEqual(fixtures._json(response)["error"]["code"], "migration_unavailable")
        self.command.assert_called_once()
        unsafe = "private-secret email@example.com sourceRef cuevion:collab:"
        with patch.object(discovery_migration, "run_runtime_apply_page", side_effect=RuntimeError(unsafe)):
            response = self.response()
        self.assertEqual(fixtures._json(response)["error"]["code"], "migration_apply_failed")
        self.assertNotIn(unsafe, response.body.decode())
        self.assertEqual(captured.getvalue(), "")

    def test_three_enrolled_result_is_projected_and_never_hardcoded(self):
        for count in (0, 1, 3, 5):
            result = {**dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": False,
                      "examined": count, "enrolled": count, "scanCalls": 1,
                      "hasMore": False, "done": True, "status": "ok", "error": None}
            with patch.object(discovery_migration, "run_runtime_apply_page", return_value=result) as apply:
                response = self.response()
            apply.assert_called_once()
            self.assertEqual(response.status, 200)
            self.assertEqual(fixtures._json(response)["data"], {k: v for k, v in result.items() if k != "error"})

    def test_skips_capacity_retry_deferred_and_has_more_never_launch_page_two(self):
        for outcome in COUNTERS[3:]:
            blocked = outcome in {"capacity", "retry"}
            pending = blocked or outcome == "deferred"
            result = {**dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": False,
                      "examined": 1, outcome: 1, "scanCalls": 1,
                      "hasMore": pending, "done": not pending, "status": "blocked" if blocked else "ok",
                      "error": {"code": outcome} if blocked else None}
            with self.subTest(outcome=outcome), patch.object(
                    discovery_migration, "run_runtime_apply_page", return_value=result) as apply:
                response = self.response()
            self.assertEqual(response.status, 200)
            self.assertEqual(set(fixtures._json(response)["data"]), OUTPUT_FIELDS)
            apply.assert_called_once()
        self.command.return_value = {"status": "ok", "result": ["987654321", []]}
        response = self.response()
        self.assertTrue(fixtures._json(response)["data"]["hasMore"])
        self.assertFalse(fixtures._json(response)["data"]["done"])
        self.assertNotIn("987654321", response.body.decode())
        self.command.assert_called_once()

    def test_strict_projection_rejects_identity_content_cursor_errors_and_wrong_mode(self):
        valid = {**dict.fromkeys(COUNTERS, 0), "v": 1, "dryRun": False, "scanCalls": 1,
                 "hasMore": False, "done": True, "status": "ok", "error": None}
        unsafe = "email@example.com private-message sourceRef cuevion:collab: secret"
        for mutation in ({"v": True}, {"dryRun": True}, {"dryRun": 0}, {"examined": 6},
                         {"enrolled": 1}, {"enrolled": True}, {"scanCalls": 2}, {"hasMore": "false"},
                         {"done": False}, {"status": unsafe}, {"error": {"code": unsafe}},
                         {"cursor": unsafe}, {"collaborationId": unsafe}, {"sourceRef": unsafe},
                         {"messages": [unsafe]}, {"participant": unsafe}, {"guest": unsafe},
                         {"email": unsafe}, {"wouldEnroll": 0}, {"alreadyPlanned": 0}):
            with self.subTest(fields=tuple(mutation)), patch.object(
                    discovery_migration, "run_runtime_apply_page", return_value={**valid, **mutation}):
                response = self.response()
            self.assertEqual(response.status, 503)
            self.assertNotIn(unsafe, response.body.decode())

    def test_startup_summary_guest_and_grant_paths_do_not_import_migration(self):
        script = '''
import sys
from unittest.mock import patch
from api.collaboration import owner, owner_http, guest, guest_http, operator_grant
from api.collaboration import test_owner_http as f
assert 'api.collaboration.discovery_migration' not in sys.modules
env={**f._environment(),operator_grant.MODE_ENV:'runtime_apply'}
ctx=f._context()
cfg=f.parse_owner_security_configuration(owner_http._trusted_security_snapshot(env))
csrf=f.issue_owner_csrf_token(ctx,cfg,now=f.NOW)[0]
with patch.object(owner_http,'_resolve_context',return_value=ctx), patch.object(owner_http,'_rate_limit_response',return_value=None), patch.object(owner_http.application,'list_v2_summaries_for_verified_owner',return_value={}):
    for payload in ({'operation':'list_summaries','cursor':None},{'operation':'issue_migration_operator_grant'},{'operation':'run_discovery_migration_dry_run_page','ownerMailboxId':f.MAILBOX_ID}):
        owner_http.owner_response(f._request(payload,csrf=csrf),http_mode='owner_write',environment=env,now=f.NOW)
assert 'api.collaboration.discovery_migration' not in sys.modules
from api.collaboration import test_guest_http as g
response=g._invoke(g._post({'operation':'apply_discovery_migration_page','ownerMailboxId':f.MAILBOX_ID,'confirmation':'APPLY_HISTORICAL_DISCOVERY'}))
assert response.status != 200
assert 'api.collaboration.discovery_migration' not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
