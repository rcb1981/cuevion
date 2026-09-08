from __future__ import annotations

from dataclasses import replace
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from . import authorization as auth, discovery_migration as migration, models, redis_store as store
from . import test_discovery_migration as manual
from . import test_lua_redis_integration as fixtures
from . import test_owner_http as http
from . import test_summary as summary


READS = ("type", "strlen", "mget", "pttl", "get", "hget", "hlen", "hkeys", "exists")
WRITES = ("hset", "hdel", "set", "del", "pexpire", "expire", "persist", "psetex")
COUNTERS = ("examined", "wouldEnroll", "alreadyPresent", "alreadyPlanned", "skippedInvalid",
            "skippedUnauthorized", "unresolvedIdentity", "staleEntitlement", "missing",
            "capacity", "retry", "deferred")


class RuntimeDiscoveryMigrationTests(unittest.TestCase):
    """Runtime adapter and unchanged Lua against a private Unix-socket Redis."""

    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    setUp = manual.DiscoveryMigrationTests.setUp
    transport = manual.DiscoveryMigrationTests.transport
    seed = manual.DiscoveryMigrationTests.seed
    index = manual.DiscoveryMigrationTests.index
    snapshot = manual.DiscoveryMigrationTests.snapshot
    fill_index = manual.DiscoveryMigrationTests.fill_index
    command_counts = manual.DiscoveryMigrationTests.command_counts

    def run_runtime(self, **overrides):
        options = {"owner_mailbox_id": http.MAILBOX_ID,
                   "owner_security_configuration": self.config,
                   "command_transport": self.transport}
        options.update(overrides)
        return migration.run_runtime_dry_run_page(self.context, (), **options)

    def all_snapshot_keys(self, values):
        pointers = [store.build_v2_source_thread_key(
            value["ownerEmail"], value["mailboxId"], value["sourceRef"]) for value in values]
        return self.keys + pointers + [self.index()]

    def assert_safe(self, result):
        self.assertEqual(set(result), set(COUNTERS) | {
            "v", "dryRun", "scanCalls", "hasMore", "done", "status", "error"})
        self.assertIs(result["dryRun"], True)
        self.assertEqual(result["v"], 1)
        self.assertLessEqual(result["scanCalls"], 1)
        self.assertLessEqual(result["examined"], 5)
        self.assertEqual(sum(result[name] for name in COUNTERS[1:]), result["examined"])
        self.assertEqual(result["done"], not result["hasMore"])
        encoded = json.dumps(result)
        for private in ("cuevion:", "collaborationId", "sourceRef", "owner@example.com",
                        "bodyText", "participants", "guest", "secret", "nextCursor"):
            self.assertNotIn(private, encoded)
        for key in self.keys:
            self.assertNotIn(key.removeprefix(store.V2_THREAD_KEY_PREFIX), encoded)

    def test_healthy_five_candidate_page_counts_and_all_bytes_and_ttls_unchanged(self):
        states = ("needs_review", "needs_action", "note_only", "resolved", "needs_review")
        values = [self.seed(i, legacy=i == 0, state=states[i]) for i in range(5)]
        snapshot_keys = self.all_snapshot_keys(values)
        before = self.snapshot(snapshot_keys)
        counters_before = self.command_counts(("scan", "eval") + READS + WRITES)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
             patch.object(migration, "_save", side_effect=AssertionError("no checkpoint")), \
             patch.object(migration.os, "open", side_effect=AssertionError("no filesystem")):
            result = self.run_runtime()
        counters_after = self.command_counts(counters_before)
        delta = {name: counters_after[name] - counters_before[name] for name in counters_before}
        self.assertEqual((result["wouldEnroll"], result["scanCalls"], result["done"]), (5, 1, True), result)
        self.assertEqual(self.snapshot(snapshot_keys), before)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual((delta["scan"], delta["eval"], delta["hkeys"], delta["exists"]), (1, 2, 0, 0))
        self.assertEqual(sum(delta[name] for name in WRITES), 0, delta)
        self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
        self.assertEqual(self.commands[0], ["SCAN", "0", "MATCH", migration.SCAN_PATTERN, "COUNT", 100])
        for command in self.commands[1:]:
            config = json.loads(command[3 + command[2]])
            self.assertIs(config["dryRun"], True)
            self.assertLessEqual(command[2], 15)
        self.assert_safe(result)
        print("Local isolated runtime five-candidate commands: " + json.dumps({
            "SCAN": delta["scan"], "EVAL": delta["eval"],
            "nestedReads": sum(delta[name] for name in READS),
            "readBreakdown": {name: delta[name] for name in READS},
            "HKEYS": delta["hkeys"], "EXISTS": delta["exists"],
            "writes": sum(delta[name] for name in WRITES)}, sort_keys=True))

    def test_count_overshoot_duplicates_and_nonzero_cursor_are_one_page_only(self):
        for i in range(8):
            self.seed(i)
        for next_cursor in ("0", "123456789"):
            with self.subTest(next_cursor=next_cursor):
                self.commands.clear()
                self.scan_responses = [[next_cursor, self.keys + self.keys], ["0", self.keys]]
                result = self.run_runtime()
                self.assertEqual((result["examined"], result["wouldEnroll"], result["scanCalls"]), (5, 5, 1))
                self.assertTrue(result["hasMore"])
                self.assertFalse(result["done"])
                self.assertEqual(len(self.scan_responses), 1)
                self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
                self.assert_safe(result)

    def test_empty_nonterminal_scan_never_retries(self):
        for next_cursor, done in (("0", True), ("17", False)):
            with self.subTest(next_cursor=next_cursor):
                self.commands.clear()
                self.scan_responses = [[next_cursor, []], ["0", []]]
                result = self.run_runtime()
                self.assertEqual((result["scanCalls"], result["examined"], result["done"]), (1, 0, done))
                self.assertEqual([c[0] for c in self.commands], ["SCAN"])
                self.assertEqual(len(self.scan_responses), 1)
                self.assert_safe(result)

    def test_runtime_has_no_apply_budget_cursor_or_checkpoint_arguments(self):
        for key, value in (("dry_run", False), ("apply", True), ("scan_budget", 2),
                           ("cursor", "0"), ("checkpoint_path", "/tmp/unused"), ("enabled", True)):
            with self.subTest(key=key), self.assertRaises(TypeError):
                self.run_runtime(**{key: value})
        self.assertEqual(self.commands, [])

    def test_unauthenticated_noncanonical_and_wrong_workspace_stop_before_scan(self):
        original_context, original_member = self.context, self.member
        self.context = object()
        self.assertEqual(self.run_runtime()["error"]["code"], "owner_not_authorized")
        self.context = original_context
        for change in ({"user_id": "legacy-user"}, {"workspace_id": "wsp_" + "Z" * 22},
                       {"email": "rebound@example.com"}):
            self.member = replace(original_member, **change)
            self.assertEqual(self.run_runtime()["error"]["code"], "owner_not_authorized")
        self.assertEqual(self.commands, [])

    def test_missing_unallowlisted_invalid_and_unowned_mailboxes_stop_before_scan(self):
        for mailbox in (None, "", "Wrong", "mailbox-other", " owner-mailbox"):
            result = self.run_runtime(owner_mailbox_id=mailbox)
            self.assertEqual(result["error"]["code"], "mailbox_not_authorized")
        self.mailbox.side_effect = lambda *a, **k: {"status": "not_found"}
        self.assertEqual(self.run_runtime()["error"]["code"], "owner_not_authorized")
        self.assertEqual(self.commands, [])

    def test_current_custom_imap_provider_is_supported_and_wrong_provider_stops_before_scan(self):
        self.provider = "custom_imap"
        self.seed(sourceRef={"provider": "custom_imap", "folder": "INBOX", "uidValidity": "12", "imapUid": "34"})
        self.scan_responses = [["0", self.keys]]
        self.assertEqual(self.run_runtime()["wouldEnroll"], 1)
        self.commands.clear()
        self.provider = "unknown"
        self.assertEqual(self.run_runtime()["error"]["code"], "authority_unavailable")
        self.assertEqual(self.commands, [])

    def test_account_mailbox_team_and_index_configuration_failures_stop_before_scan(self):
        with patch.object(auth, "_resolve_current_authenticated_member", return_value=(None, "unavailable")):
            self.assertEqual(self.run_runtime()["error"]["code"], "authority_unavailable")
        self.mailbox.side_effect = lambda *a, **k: {"status": "unavailable"}
        self.assertEqual(self.run_runtime()["error"]["code"], "authority_unavailable")
        self.team.side_effect = lambda *a: (None, "unavailable")
        self.assertEqual(self.run_runtime()["error"]["code"], "authority_unavailable")
        self.team.side_effect = lambda *a: (self.membership, None)
        self.mailbox.side_effect = lambda *a, **k: {
            "status": "ok", "user": {"email": self.member.email}, "memberAuthority": self.member,
            "inbox": {"id": http.MAILBOX_ID, "provider": "google"}}
        with patch.object(store, "resolve_v2_index_hmac_keys", return_value=None):
            self.assertEqual(self.run_runtime()["error"]["code"], "migration_unavailable")
        self.assertEqual(self.commands, [])

    def test_owner_without_team_and_exact_current_participant_entitlement(self):
        self.seed(1)
        self.team.side_effect = lambda *a: (None, "not_active")
        self.scan_responses = [["0", self.keys]]
        self.assertEqual(self.run_runtime()["wouldEnroll"], 1)
        self.seed(2, ownerEmail="another@example.com", ownerUserId=summary.OTHER,
                  participants=[{"userId": self.member.user_id, "displayName": "Owner Person",
                                 "membershipRef": "tinv_original"}])
        before = self.snapshot(self.keys + [self.index()])
        for reference, expected in ((None, "staleEntitlement"), ("tinv_rebound", "staleEntitlement"),
                                    ("tinv_original", "wouldEnroll")):
            if reference is not None:
                self.team.side_effect = lambda *a, ref=reference: (
                    {"memberUserId": self.member.user_id, "sourceInvitationId": ref}, None)
            self.scan_responses = [["0", [self.keys[-1]]]]
            result = self.run_runtime()
            self.assertEqual(result[expected], 1, result)
            self.assert_safe(result)
            self.assertEqual(self.snapshot(self.keys + [self.index()]), before)

    def test_scan_and_eval_failures_are_fixed_safe_errors_without_retry(self):
        self.seed()
        for failure_step in ("SCAN", "EVAL"):
            calls = []
            def failing(command):
                calls.append(command[0])
                if command[0] == failure_step:
                    raise RuntimeError("private-secret-content")
                return {"result": ["0", self.keys]}
            result = self.run_runtime(command_transport=failing)
            self.assertEqual(result["error"]["code"], "migration_unavailable")
            self.assertEqual(calls, ["SCAN"] if failure_step == "SCAN" else ["SCAN", "EVAL"])
            self.assertEqual(result["scanCalls"], 1)
            self.assert_safe(result)

    def test_scan_malformed_namespace_cursor_and_overflow_are_safe_and_bounded(self):
        for response in (["0", ["private-key"]], ["-1", []], ["0", self.keys * 0 + [migration.SCAN_PATTERN] * 1001]):
            self.commands.clear()
            self.scan_responses = [response]
            result = self.run_runtime()
            self.assertEqual(result["error"]["code"], "migration_dry_run_failed")
            self.assertEqual([c[0] for c in self.commands], ["SCAN"])
            self.assert_safe(result)

    def test_malformed_historical_record_is_aggregate_skip_with_no_writes(self):
        value = self.seed()
        self.client.command(["SET", self.keys[0], '{"private-secret-content":true}', "KEEPTTL"])
        keys = self.all_snapshot_keys([value])
        before = self.snapshot(keys)
        self.scan_responses = [["0", self.keys]]
        result = self.run_runtime()
        self.assertEqual((result["skippedInvalid"], result["done"]), (1, True), result)
        self.assertEqual(self.snapshot(keys), before)
        self.assert_safe(result)

    def test_capacity_is_aggregate_only_and_never_prunes_or_writes(self):
        self.fill_index(1000)
        values = [self.seed(i, legacy=i == 0) for i in range(5)]
        snapshot_keys = self.all_snapshot_keys(values)
        before = self.snapshot(snapshot_keys)
        self.scan_responses = [["0", self.keys]]
        counters_before = self.command_counts(READS + WRITES)
        result = self.run_runtime()
        counters_after = self.command_counts(counters_before)
        self.assertEqual((result["capacity"], result["status"], result["hasMore"]), (5, "blocked", True), result)
        self.assertEqual(self.snapshot(snapshot_keys), before)
        self.assertEqual(counters_after["exists"] - counters_before["exists"], 1000)
        self.assertEqual(counters_after["hkeys"] - counters_before["hkeys"], 2)
        self.assertEqual({name: counters_after[name] for name in WRITES},
                         {name: counters_before[name] for name in WRITES})
        self.assert_safe(result)

    def test_existing_discovery_entry_and_prunable_capacity_remain_unchanged(self):
        self.fill_index(1)
        value = self.seed(10_000)
        keys = self.all_snapshot_keys([value])
        before = self.snapshot(keys)
        self.scan_responses = [["0", self.keys]]
        self.assertEqual(self.run_runtime()["alreadyPresent"], 1)
        self.assertEqual(self.snapshot(keys), before)
        self.client.command(["FLUSHALL"])
        self.keys.clear()
        self.fill_index(1000, present=False)
        value = self.seed(1, legacy=True)
        keys = self.all_snapshot_keys([value])
        before = self.snapshot(keys)
        counters_before = self.command_counts(WRITES)
        self.scan_responses = [["0", self.keys]]
        result = self.run_runtime()
        self.assertEqual(result["wouldEnroll"], 1, result)
        self.assertEqual(self.command_counts(WRITES), counters_before)
        self.assertEqual(self.snapshot(keys), before)
        self.assert_safe(result)

    def test_large_records_keep_existing_aggregate_byte_bound_and_do_not_continue(self):
        values = [self.seed(i, legacy=i == 0,
            sourceMessage={**summary.thread()["sourceMessage"], "bodyText": "b" * models.MAX_V2_SOURCE_BODY},
            messages=[fixtures.message_record(index=j, created_at=summary.thread()["updatedAt"], text="m" * 16000)
                      for j in range(7)]) for i in range(5)]
        snapshot_keys = self.all_snapshot_keys(values)
        before = self.snapshot(snapshot_keys)
        self.scan_responses = [["0", self.keys]]
        result = self.run_runtime()
        self.assertEqual((result["examined"], result["wouldEnroll"], result["deferred"]), (5, 1, 4), result)
        self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
        self.assertEqual(self.snapshot(snapshot_keys), before)
        self.assertTrue(result["hasMore"])
        self.assert_safe(result)
