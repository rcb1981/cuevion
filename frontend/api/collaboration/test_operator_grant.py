from __future__ import annotations

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from api import user_config_store
from api.auth.runtime import AuthenticatedMemberContext
from . import http_adapter, operator_grant as grant, owner_http, owner_rate_limit, owner_request_security as security, redis_store
from . import test_owner_http as fixtures

WORKSPACE = "wsp_" + "A" * 22
USER = "usr_" + "A" * 22
NOW = fixtures.NOW


def configuration():
    return security.parse_owner_security_configuration(owner_http._trusted_security_snapshot(fixtures._environment()))


def member():
    return AuthenticatedMemberContext(USER, fixtures.OWNER_EMAIL, "Owner Person", WORKSPACE, "owner")


def config_record(**updates):
    value = {"email": fixtures.OWNER_EMAIL, "managedInboxes": [
        {"id": fixtures.MAILBOX_ID, "provider": "google", "email": "mailbox@example.com",
         "connected": True, "connectionStatus": "connected", "accessToken": "fixture-credential",
         "customImap": {"password": "fixture-password"}},
    ]}
    value.update(updates)
    return {"status": "ok", "config": value, "error": None}


def issued():
    return grant._issue_operator_grant(member(), [{"mailboxId": fixtures.MAILBOX_ID, "provider": "google"}],
                                     owner_security_configuration=configuration(), now=NOW)


class OperatorGrantCryptoTests(unittest.TestCase):
    def setUp(self):
        self.configuration = configuration()
        self.token = issued()["grant"]
        self.payload = json.loads(grant._unb64(self.token.split(".")[1]))

    def verify(self, token=None, **kwargs):
        return grant.verify_operator_grant(self.token if token is None else token,
                                          owner_security_configuration=self.configuration, now=kwargs.get("now", NOW))

    def signed(self, payload=None, raw=None):
        segment = grant._b64((grant._json(payload) if raw is None else raw).encode("ascii"))
        return "mog1." + segment + "." + grant._b64(grant._signature(self.configuration, segment))

    def rejects(self, token, *, now=NOW, code=None):
        with self.assertRaises(grant.OperatorGrantError) as raised:
            self.verify(token, now=now)
        if code is not None:
            self.assertEqual(raised.exception.code, code)

    def test_valid_signature_mints_opaque_dry_run_context_only(self):
        context = self.verify()
        self.assertIs(type(context), grant.VerifiedMigrationOperatorContext)
        self.assertEqual((context.user_id, context.workspace_id), (USER, WORKSPACE))
        self.assertEqual(context.mailboxes, ((fixtures.MAILBOX_ID, "google"),))
        self.assertFalse(security._is_owner_context(context))
        self.assertEqual(repr(context), "<VerifiedMigrationOperatorContext>")
        with self.assertRaises(TypeError):
            grant.VerifiedMigrationOperatorContext()
        with self.assertRaises(TypeError):
            context.user_id = "arbitrary"
        with self.assertRaises(TypeError):
            pickle.dumps(context)

    def test_payload_is_exact_safe_projection(self):
        self.assertEqual(set(self.payload), grant._FIELDS)
        self.assertEqual(self.payload["purpose"], "collaboration_discovery_migration_dry_run")
        self.assertEqual(self.payload["expiresAt"] - self.payload["issuedAt"], 600)
        self.assertEqual(set(self.payload["mailboxes"][0]), {"mailboxId", "provider"})
        raw = grant._json(self.payload)
        for forbidden in ("fixture-credential", "fixture-password", fixtures.OWNER_EMAIL, fixtures.SESSION_ID,
                          fixtures.CREDENTIAL_DIGEST, fixtures.ISSUER, fixtures.SUBJECT, "sourceRef",
                          "participants", "accessToken", "cookie", "csrf", "redis", "email"):
            self.assertNotIn(forbidden, raw)

    def test_payload_mutation_without_resigning_fails(self):
        parts = self.token.split(".")
        raw = bytearray(grant._unb64(parts[1]))
        raw[10] ^= 1
        parts[1] = grant._b64(bytes(raw))
        self.rejects(".".join(parts))

    def test_signature_mutation_fails(self):
        parts = self.token.split(".")
        raw = bytearray(grant._unb64(parts[2]))
        raw[0] ^= 1
        parts[2] = grant._b64(bytes(raw))
        self.rejects(".".join(parts))

    def test_domain_separation_rejects_csrf_and_direct_root_signatures(self):
        import hashlib
        import hmac
        context = fixtures._context(workspace_id=WORKSPACE)
        token, _ = security.issue_owner_csrf_token(context, self.configuration, now=NOW)
        self.rejects(token)
        segment = self.token.split(".")[1]
        root_signature = hmac.new(fixtures.CSRF_KEY, ("mog1." + segment).encode(), hashlib.sha256).digest()
        self.rejects("mog1." + segment + "." + grant._b64(root_signature))
        self.assertNotEqual(grant._signature(self.configuration, segment), root_signature)

    def test_constant_time_signature_comparison_is_used(self):
        with patch.object(grant.hmac, "compare_digest", wraps=grant.hmac.compare_digest) as compare:
            self.verify()
        self.assertTrue(any(type(call.args[0]) is bytes and len(call.args[0]) == 32 for call in compare.call_args_list))

    def test_unknown_purpose_version_fields_and_unsigned_forms_rejected(self):
        for updates in ({"purpose": "collaboration_discovery_migration_apply"}, {"purpose": "owner"},
                        {"version": 2}, {"version": True}, {"dryRun": True}, {"userEmail": "owner@example.com"}):
            with self.subTest(updates=updates):
                self.rejects(self.signed({**self.payload, **updates}))
        for token in (self.token.rsplit(".", 1)[0], grant._json(self.payload), "", None, self.token + ".x"):
            with self.subTest(kind=type(token).__name__):
                if token is None:
                    with self.assertRaises(grant.OperatorGrantError):
                        grant.verify_operator_grant(None, owner_security_configuration=self.configuration, now=NOW)
                else:
                    self.rejects(token)

    def test_expiry_future_and_lifetime_are_strict(self):
        self.verify(now=NOW + 599)
        self.rejects(self.token, now=NOW + 600, code="grant_expired")
        self.rejects(self.token, now=NOW - 1)
        for updates in ({"expiresAt": NOW + 601}, {"expiresAt": NOW}, {"issuedAt": -1},
                        {"issuedAt": True}, {"expiresAt": float(NOW + 600)}, {"expiresAt": str(NOW + 600)},
                        {"issuedAt": NOW + 1}, {"expiresAt": 2**53}):
            with self.subTest(updates=updates):
                self.rejects(self.signed({**self.payload, **updates}))

    def test_duplicate_top_level_and_nested_keys_rejected(self):
        raw = grant._json(self.payload)
        self.rejects(self.signed(raw='{"version":1,' + raw[1:]))
        self.rejects(self.signed(raw=raw.replace('"provider":"google"', '"provider":"google","provider":"google"')))

    def test_canonical_wire_encoding_and_depth_bounds(self):
        self.rejects(self.signed(raw=json.dumps(self.payload, indent=2)))
        self.rejects(self.signed(raw=grant._json(self.payload).replace('"version":1', '"version":1e0')))
        self.rejects(self.signed(raw=grant._json(self.payload).replace('"version":1', '"version":NaN')))
        self.rejects(self.signed(raw='{"version":' + '[' * 1100 + '0' + ']' * 1100 + '}'))
        self.rejects("x" * (grant.MAX_GRANT_BYTES + 1))
        for position in (1, 2):
            parts = self.token.split(".")
            parts[position] += "="
            self.rejects(".".join(parts))

    def test_canonical_ids_and_mailbox_schema_required(self):
        for updates in ({"userId": "owner@example.com"}, {"userId": "usr_" + "B" * 22},
                        {"workspaceId": "wsp_" + "B" * 22}, {"workspaceId": USER}, {"userId": True}):
            self.rejects(self.signed({**self.payload, **updates}))
        for scopes in ([], {}, [{"mailboxId": "OTHER", "provider": "google"}],
                       [{"mailboxId": fixtures.MAILBOX_ID, "provider": "outlook"}],
                       [{"mailboxId": fixtures.MAILBOX_ID, "provider": "google", "token": "x"}],
                       self.payload["mailboxes"] * 2,
                       [{"mailboxId": "b", "provider": "google"}, {"mailboxId": "a", "provider": "google"}]):
            self.rejects(self.signed({**self.payload, "mailboxes": scopes}))

    def test_nonce_binding_and_current_rollout_are_required(self):
        for updates in ({"nonce": "A" * 42}, {"configurationBinding": "A" * 43}):
            self.rejects(self.signed({**self.payload, **updates}))
        env = fixtures._environment(mailbox_id="removed.mailbox")
        changed = security.parse_owner_security_configuration(owner_http._trusted_security_snapshot(env))
        with self.assertRaises(grant.OperatorGrantError) as raised:
            grant.verify_operator_grant(self.token, owner_security_configuration=changed, now=NOW)
        self.assertEqual(raised.exception.code, "owner_not_authorized")

    def test_replay_is_bounded_by_same_expiry(self):
        for offset in (0, 1, 599):
            self.assertEqual(self.verify(now=NOW + offset).expires_at, NOW + 600)
        self.rejects(self.token, now=NOW + 600, code="grant_expired")

    def test_allowlist_root_rotation_revokes_grant_with_unchanged_entries(self):
        env = fixtures._environment()
        env["CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"] = grant._b64(b"rotated-allowlist-root-material-32!")
        changed = security.parse_owner_security_configuration(owner_http._trusted_security_snapshot(env))
        self.assertFalse(security.owner_is_allowlisted(fixtures._context(workspace_id=WORKSPACE), changed))
        with self.assertRaises(grant.OperatorGrantError) as raised:
            grant.verify_operator_grant(self.token, owner_security_configuration=changed, now=NOW)
        self.assertEqual(raised.exception.code, "owner_not_authorized")


class OperatorGrantHttpTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.context = fixtures._context(workspace_id=WORKSPACE)
        self.csrf = security.issue_owner_csrf_token(self.context, self.config, now=NOW)[0]
        self.environment = {**fixtures._environment(), grant.MODE_ENV: "dry_run_grant"}
        self.read_result = (member(), {"email": fixtures.OWNER_EMAIL}, config_record())
        self.reader = Mock(side_effect=lambda _: self.read_result)
        self.limiter = Mock(return_value=owner_rate_limit.OwnerRateLimitDecision("allowed"))
        for patcher in (patch.object(owner_http, "_resolve_context", return_value=self.context),
                        patch.object(user_config_store, "read_user_config_for_authenticated_member", self.reader),
                        patch.object(owner_rate_limit, "consume_owner_rate_limit", self.limiter)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, request=None, *, mode="owner_read"):
        return http_adapter.invoke_safely(lambda: owner_http.owner_response(
            request or fixtures._request({"operation": "issue_migration_operator_grant"}, csrf=self.csrf),
            http_mode=mode, environment=self.environment, now=NOW), allow_method="POST")

    def test_authenticated_owner_issues_server_derived_scopes_without_logging(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            response = self.response()
        self.assertEqual(response.status, 200)
        self.assertEqual(output.getvalue(), "")
        data = fixtures._json(response)["data"]
        context = grant.verify_operator_grant(data["grant"], owner_security_configuration=self.config, now=NOW)
        self.assertEqual((context.user_id, context.workspace_id), (USER, WORKSPACE))
        self.assertEqual(context.mailboxes, ((fixtures.MAILBOX_ID, "google"),))
        headers = dict(response.headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.limiter.assert_called_once_with(self.context, owner_rate_limit.RATE_LIMIT_OPERATOR_GRANT,
                                             unittest.mock.ANY, operator_user_id=USER)
        for secret in ("fixture-credential", "fixture-password", fixtures.SESSION_ID, fixtures.CREDENTIAL_DIGEST):
            self.assertNotIn(secret, response.body.decode())

    def test_absent_off_unknown_modes_deny_without_authority_or_rate_commands(self):
        for mode in (None, "off", "dry_run", "DRY_RUN_GRANT", "dry_run_grant ", True):
            self.reader.reset_mock()
            self.limiter.reset_mock()
            if mode is None:
                self.environment.pop(grant.MODE_ENV, None)
            else:
                self.environment[grant.MODE_ENV] = mode
            self.assertEqual(self.response().status, 404)
            self.reader.assert_not_called()
            self.limiter.assert_not_called()

    def test_external_guest_unauthenticated_expired_and_revoked_denied(self):
        for kind in ("guest", "missing", "expired", "revoked"):
            with self.subTest(kind=kind), patch.object(owner_http, "_resolve_context",
                side_effect=security.OwnerSecurityError("authentication_required")):
                response = self.response()
                self.assertEqual(response.status, 401)
        self.reader.assert_not_called()
        self.limiter.assert_not_called()

    def test_real_owner_authentication_without_cookie_denies_guest_headers(self):
        # Exercise the actual adapter/runtime; a guest cookie is never an owner session.
        with patch.object(owner_http, "_resolve_context", wraps=lambda headers, **kw:
            security.resolve_owner_request_context(headers, authentication_resolver=lambda received:
                owner_http.resolve_verified_auth0_owner(received, environment={}, now=NOW), now=NOW)):
            request = fixtures._request({"operation": "issue_migration_operator_grant"}, csrf=self.csrf)
            request.headers.pairs.append(("Cookie", "cuevion_collab_guest=guest-fixture"))
            self.assertEqual(self.response(request).status, 401)
        self.reader.assert_not_called()

    def test_wrong_workspace_email_and_name_context_denied(self):
        for updates in ({"workspace_id": "wsp_" + "Q" * 21 + "A"}, {"owner_email": "other@example.com"},
                        {"display_name": "Someone Else"}):
            context = fixtures._context(**{**{"workspace_id": WORKSPACE}, **updates})
            csrf = security.issue_owner_csrf_token(context, self.config, now=NOW)[0]
            with patch.object(owner_http, "_resolve_context", return_value=context):
                self.assertEqual(self.response(fixtures._request({"operation": "issue_migration_operator_grant"}, csrf=csrf)).status, 403)
        self.limiter.assert_not_called()

    def test_submitted_identity_scope_or_migration_options_denied(self):
        for extra in ({"userId": USER}, {"workspaceId": WORKSPACE}, {"mailboxId": fixtures.MAILBOX_ID},
                      {"scan_budget": 25}, {"dry_run": True}, {"cursor": None}):
            response = self.response(fixtures._request({"operation": "issue_migration_operator_grant", **extra}, csrf=self.csrf))
            self.assertEqual(response.status, 400)
        self.reader.assert_not_called()

    def test_origin_csrf_method_and_content_type_preserved(self):
        requests = [fixtures._request({"operation": "issue_migration_operator_grant"}),
                    fixtures._request({"operation": "issue_migration_operator_grant"}, csrf="invalid"),
                    fixtures._request({"operation": "issue_migration_operator_grant"}, csrf=self.csrf, method="GET")]
        for header, value in (("Origin", "https://attacker.example"), ("Content-Type", "text/plain")):
            request = fixtures._request({"operation": "issue_migration_operator_grant"}, csrf=self.csrf)
            request.headers.pairs = [(key, value if key == header else old) for key, old in request.headers.pairs]
            requests.append(request)
        for request in requests:
            self.assertIn(self.response(request).status, (400, 403, 405, 415))
        self.reader.assert_not_called()
        self.limiter.assert_not_called()

    def test_rate_limit_and_unavailable_limiter_never_sign(self):
        with patch.object(grant, "_issue_operator_grant") as sign:
            for decision, status in ((owner_rate_limit.OwnerRateLimitDecision("limited", 60), 429),
                                     (owner_rate_limit.OwnerRateLimitDecision("unavailable"), 503)):
                self.limiter.return_value = decision
                response = self.response()
                self.assertEqual(response.status, status)
                if status == 429:
                    self.assertEqual(dict(response.headers)["Retry-After"], "60")
            sign.assert_not_called()

    def test_mailboxes_are_server_read_allowlisted_and_unambiguous(self):
        for updates, status in (({"managedInboxes": []}, 404),
                                ({"email": "other@example.com"}, 403),
                                ({"managedInboxes": config_record()["config"]["managedInboxes"] * 2}, 403)):
            self.read_result = (member(), {"email": fixtures.OWNER_EMAIL}, config_record(**updates))
            self.assertEqual(self.response().status, status)
        self.limiter.assert_not_called()

    def test_normal_http_operations_cannot_run_migration(self):
        for operation in ("run_page", "run_page_with_operator_grant", "discovery_migration", "apply"):
            self.assertEqual(self.response(fixtures._request({"operation": operation}, csrf=self.csrf), mode="owner_write").status, 400)

    def test_issuance_never_dispatches_migration_commands(self):
        with patch.object(redis_store, "_v2_command", side_effect=AssertionError("unexpected migration command")) as command:
            self.assertEqual(self.response().status, 200)
        command.assert_not_called()

    def test_startup_issuance_and_summaries_never_import_migration(self):
        script = """
import sys
from unittest.mock import patch
from api.collaboration import owner, owner_http, operator_grant
from api.collaboration import test_owner_http as f
assert 'api.collaboration.discovery_migration' not in sys.modules
ctx = f._context()
cfg = f.parse_owner_security_configuration(owner_http._trusted_security_snapshot(f._environment()))
csrf = f.issue_owner_csrf_token(ctx, cfg, now=f.NOW)[0]
with patch.object(owner_http, '_resolve_context', return_value=ctx), patch.object(owner_http, '_rate_limit_response', return_value=None), patch.object(owner_http.application, 'list_v2_summaries_for_verified_owner', return_value={}):
    f._invoke(f._request({'operation':'list_summaries','cursor':None},csrf=csrf))
assert 'api.collaboration.discovery_migration' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)


class PrivateOperatorGrantFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="operator-grant-test-")
        self.addCleanup(self.directory.cleanup)
        self.parent = Path(self.directory.name).resolve()
        self.path = self.parent / "grant.json"
        self.data = issued()
        self.path.write_text(json.dumps({"ok": True, "data": self.data}), encoding="ascii")
        self.path.chmod(0o600)

    def test_direct_private_http_capture_is_read_without_printing(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            token = grant.read_private_operator_grant(str(self.path))
        self.assertEqual(token, self.data["grant"])
        self.assertEqual(output.getvalue(), "")

    def test_file_and_parent_modes_symlinks_and_relative_paths_denied(self):
        self.path.chmod(0o644)
        with self.assertRaises(grant.OperatorGrantError):
            grant.read_private_operator_grant(self.path)
        self.path.chmod(0o600)
        self.parent.chmod(0o755)
        with self.assertRaises(grant.OperatorGrantError):
            grant.read_private_operator_grant(self.path)
        self.parent.chmod(0o700)
        link = self.parent / "link.json"
        link.symlink_to(self.path)
        for path in (link, "relative.json"):
            with self.assertRaises(grant.OperatorGrantError):
                grant.read_private_operator_grant(path)

    def test_duplicate_response_keys_and_unknown_fields_denied(self):
        for value in ('{"ok":true,"ok":true,"data":{}}',
                      '{"ok":true,"data":{"grant":"x","expiresAt":1,"cookie":"x"}}',
                      "x" * (grant.MAX_GRANT_BYTES + 257)):
            self.path.write_text(value)
            with self.assertRaises(grant.OperatorGrantError):
                grant.read_private_operator_grant(self.path)

    def test_symlink_ancestor_is_rejected_during_directory_traversal(self):
        alias = self.parent / "alias"
        alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(grant.OperatorGrantError):
            grant.read_private_operator_grant(alias / self.path.name)
