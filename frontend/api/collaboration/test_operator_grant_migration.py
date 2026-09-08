from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from api.auth.runtime import AuthenticatedMemberContext
from . import discovery_migration as migration, operator_grant, redis_store as store
from . import test_lua_redis_integration as fixtures
from . import test_owner_http as http
from . import test_summary as summary


USER_ID = "usr_" + "A" * 22
WORKSPACE_ID = "wsp_" + "A" * 22


class OperatorGrantMigrationTests(unittest.TestCase):
    """Exercise the adapter against an isolated Unix-socket Redis fixture."""

    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])
        self.directory = tempfile.TemporaryDirectory(prefix="collab-grant-migration-")
        self.addCleanup(self.directory.cleanup)
        self.commands = []
        self.keys = []
        self.pointer_keys = []
        self.scan_responses = None
        self.run_number = 0
        self.configuration = http.parse_owner_security_configuration(
            http.owner_http._trusted_security_snapshot(http._environment()))
        self.operator_config = {
            "workspaceId": WORKSPACE_ID, "userId": USER_ID,
            "email": http.OWNER_EMAIL, "membershipRef": "",
            "ownerMailboxId": http.MAILBOX_ID, "ownerProvider": "google",
            "ownerDisplayName": "Owner Person",
        }
        self.now = http.NOW
        member = AuthenticatedMemberContext(
            USER_ID, http.OWNER_EMAIL, "Owner Person", WORKSPACE_ID, "owner")
        self.grant = operator_grant._issue_operator_grant(
            member, [{"mailboxId": http.MAILBOX_ID, "provider": "google"}],
            owner_security_configuration=self.configuration, now=self.now)["grant"]
        verify = operator_grant.verify_operator_grant
        self.verifier = Mock(side_effect=lambda *args, **kwargs: verify(
            *args, now=self.now, **kwargs))
        self.authority = Mock(side_effect=lambda *args, **kwargs: dict(self.operator_config))
        for context in (
            patch.dict(os.environ, {
                store.V2_INDEX_HMAC_ENV: base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
                store.V2_INDEX_HMAC_PREVIOUS_ENV: "",
            }),
            patch.object(operator_grant, "verify_operator_grant", self.verifier),
            patch.object(operator_grant, "resolve_current_operator_config", self.authority),
        ):
            context.start()
            self.addCleanup(context.stop)

    def transport(self, command):
        self.commands.append(command)
        if command[0] == "SCAN" and self.scan_responses is not None:
            return {"result": self.scan_responses.pop(0)}
        return self.client.transport(command)

    def new_path(self):
        self.run_number += 1
        return Path(self.directory.name) / f"page-{self.run_number}.json"

    def run_page(self, path=None, **kwargs):
        options = {
            "enabled": True, "checkpoint_path": path or self.new_path(),
            "owner_mailbox_id": http.MAILBOX_ID,
            "owner_security_configuration": self.configuration,
            "command_transport": self.transport,
        }
        options.update(kwargs)
        return migration.run_page_with_operator_grant(self.grant, **options)

    def seed(self, number=1, *, legacy=False, **updates):
        value = summary.thread(number, workspaceId=WORKSPACE_ID, ownerUserId=USER_ID)
        value.update(updates)
        if legacy:
            for field in ("ownerUserId", "ownerDisplayName", "participants"):
                value.pop(field)
        key = store.build_v2_thread_key(value["collaborationId"])
        pointer = store.build_v2_source_thread_key(
            value["ownerEmail"], value["mailboxId"], value["sourceRef"])
        self.client.command(["SET", key, fixtures.wire_json(value, "thread"), "PX", 300_000])
        self.client.command(["SET", pointer, value["collaborationId"], "PX", 300_000])
        self.keys.append(key)
        self.pointer_keys.append(pointer)
        return value

    def snapshot(self):
        keys = self.keys + self.pointer_keys + [store.build_v2_discovery_key(WORKSPACE_ID, USER_ID)]
        return [self.client.command([
            "EVAL", "local d=redis.call('DUMP',KEYS[1]); return {d and redis.sha1hex(d) or '',redis.call('PEXPIRETIME',KEYS[1])}",
            1, key,
        ]) for key in keys]

    def write_counts(self):
        raw = self.client.command(["INFO", "commandstats"])
        return {
            name: int(match.group(1)) if (
                match := re.search(r"^cmdstat_" + name + r":calls=(\d+)", raw, re.M)) else 0
            for name in ("hset", "hdel", "set", "del", "pexpire", "expire", "persist", "psetex")
        }

    def assert_blocked_without_io(self, code, **kwargs):
        path = self.new_path()
        result = self.run_page(path, **kwargs)
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["error"]["code"], code, result)
        self.assertEqual(result["scanCalls"], 0, result)
        self.assertEqual(self.commands, [])
        self.assertFalse(path.exists())
        self.assertFalse(Path(str(path) + ".lock").exists())
        self.assertNotIn(self.grant, json.dumps(result))
        return result

    def test_manual_disabled_default_fails_before_verification_or_redis(self):
        path = self.new_path()
        result = migration.run_page_with_operator_grant(
            self.grant, checkpoint_path=path, owner_mailbox_id=http.MAILBOX_ID,
            owner_security_configuration=self.configuration, command_transport=self.transport)
        self.assertEqual(result["error"]["code"], "migration_disabled")
        self.verifier.assert_not_called()
        self.authority.assert_not_called()
        self.assertEqual(self.commands, [])
        self.assertFalse(path.exists())

    def test_apply_and_non_boolean_dry_run_are_denied_before_verification(self):
        for value in (False, None, 0, 1, "true"):
            with self.subTest(value=value):
                expected = "grant_scope_invalid" if type(value) is bool else "invalid_request"
                self.assert_blocked_without_io(expected, dry_run=value)
        self.verifier.assert_not_called()
        self.authority.assert_not_called()

    def test_grant_cannot_raise_scan_budget_or_use_non_integer_budget(self):
        for value in (0, 26, 1000, 100_001, -1, True, 1.0, "25"):
            with self.subTest(value=value):
                expected = "grant_scope_invalid" if type(value) is int and 1 <= value <= 100_000 else "invalid_request"
                self.assert_blocked_without_io(expected, scan_budget=value)
        self.verifier.assert_not_called()
        self.authority.assert_not_called()

    def test_expired_or_invalid_grant_denied_before_current_authority_and_scan(self):
        for code in ("grant_expired", "grant_invalid", "grant_scope_invalid"):
            with self.subTest(code=code):
                self.verifier.side_effect = operator_grant.OperatorGrantError(code)
                self.assert_blocked_without_io(code)
        self.authority.assert_not_called()

    def test_current_account_workspace_mailbox_or_team_denial_prevents_scan(self):
        for code in ("owner_not_authorized", "grant_scope_invalid", "no_owned_mailboxes"):
            with self.subTest(code=code):
                self.authority.side_effect = operator_grant.OperatorGrantError(code)
                self.assert_blocked_without_io(code)
        self.assertEqual(self.verifier.call_count, 3)

    def test_real_expired_and_tampered_signed_grants_fail_before_scan(self):
        self.now = http.NOW + operator_grant.LIFETIME_SECONDS
        self.assert_blocked_without_io("grant_expired")
        self.now = http.NOW
        parts = self.grant.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        self.grant = ".".join(parts)
        self.assert_blocked_without_io("grant_invalid")
        self.grant = "malformed-local-grant"
        self.assert_blocked_without_io("grant_invalid")
        self.authority.assert_not_called()

    def test_unexpected_verification_error_is_fixed_and_does_not_echo_secret(self):
        self.verifier.side_effect = RuntimeError(self.grant)
        self.assert_blocked_without_io("grant_invalid")
        self.authority.assert_not_called()

    def test_verified_identity_passes_only_to_current_authority_resolver(self):
        self.scan_responses = [["0", []]]
        result = self.run_page()
        self.assertEqual((result["status"], result["dryRun"], result["done"]), ("ok", True, True), result)
        self.verifier.assert_called_once_with(
            self.grant, owner_security_configuration=self.configuration)
        self.authority.assert_called_once()
        args, kwargs = self.authority.call_args
        self.assertIs(type(args[0]), operator_grant.VerifiedMigrationOperatorContext)
        self.assertEqual((args[0].user_id, args[0].workspace_id), (USER_ID, WORKSPACE_ID))
        self.assertEqual(args[1:], (http.MAILBOX_ID,))
        self.assertEqual(kwargs, {"owner_security_configuration": self.configuration})
        self.assertEqual(sum(command[0] == "SCAN" for command in self.commands), 1)

    def test_dry_run_grant_preserves_thread_source_and_discovery_bytes_and_ttls(self):
        self.seed(legacy=True)
        self.scan_responses = [["0", self.keys]]
        before, writes = self.snapshot(), self.write_counts()
        result = self.run_page()
        self.assertEqual((result["wouldEnroll"], result["enrolled"], result["dryRun"]), (1, 0, True), result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.write_counts(), writes)
        self.assertEqual(sum(command[0] == "SCAN" for command in self.commands), 1)
        self.assertEqual(sum(command[0] == "EVAL" for command in self.commands), 2)
        for command in self.commands:
            if command[0] == "EVAL":
                arguments = json.loads(command[-1] if command[1] == migration._PROBE_LUA else command[-2])
                if command[1] == migration._ENROLL_LUA:
                    self.assertIs(arguments["dryRun"], True)
            self.assertNotIn(self.grant, json.dumps(command))

    def test_canonical_wrong_user_workspace_and_source_are_still_rejected(self):
        self.seed(1, ownerUserId="usr_" + "B" * 21 + "A")
        self.seed(2, workspaceId="wsp_" + "B" * 21 + "A")
        self.seed(3)
        self.client.command(["DEL", self.pointer_keys[2]])
        self.scan_responses = [["0", self.keys]]
        before = self.snapshot()
        result = self.run_page()
        self.assertEqual((result["skippedUnauthorized"], result["skippedInvalid"], result["enrolled"]), (2, 1, 0), result)
        self.assertEqual(self.snapshot(), before)

    def test_each_page_and_cached_replay_revalidates_grant_and_current_authority(self):
        self.seed()
        self.scan_responses = [["0", self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.commands.clear()
        self.assertEqual(self.run_page(path), first)
        self.assertEqual((self.verifier.call_count, self.authority.call_count), (2, 2))
        self.assertEqual(self.commands, [])
        self.now += operator_grant.LIFETIME_SECONDS
        expired = self.run_page(path)
        self.assertEqual(expired["error"]["code"], "grant_expired")
        self.assertEqual(self.authority.call_count, 2)
        self.assertEqual(self.commands, [])

    def test_later_page_denies_changed_current_mailbox_authority_before_pending_work(self):
        for number in range(6):
            self.seed(number)
        self.scan_responses = [["0", self.keys]]
        path = self.new_path()
        first = self.run_page(path)
        self.assertEqual((first["wouldEnroll"], first["done"]), (5, False), first)
        self.commands.clear()
        self.authority.side_effect = operator_grant.OperatorGrantError("owner_not_authorized")
        second = self.run_page(path, cursor=first["nextCursor"])
        self.assertEqual(second["error"]["code"], "owner_not_authorized")
        self.assertEqual(self.commands, [])

    def test_scan_budget_is_stored_bound_and_exhaustion_prevents_second_scan(self):
        self.scan_responses = [["17", []]]
        path = self.new_path()
        first = self.run_page(path, scan_budget=1)
        self.commands.clear()
        second = self.run_page(path, cursor=first["nextCursor"], scan_budget=1)
        self.assertEqual(second["error"]["code"], "scan_budget_exhausted")
        self.assertEqual(self.commands, [])
        changed = self.run_page(path, cursor=first["nextCursor"], scan_budget=2)
        self.assertEqual(changed["error"]["code"], "invalid_checkpoint")
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
