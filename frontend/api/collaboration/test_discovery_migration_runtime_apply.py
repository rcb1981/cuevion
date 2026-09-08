from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import re
import socket
import unittest
from unittest.mock import patch

from . import authorization as auth, discovery_migration as migration, models, redis_store as store
from . import test_discovery_migration as manual
from . import test_discovery_migration_runtime as dry
from . import test_lua_redis_integration as fixtures
from . import test_owner_http as http
from . import test_summary as summary


COUNTERS = ("examined", "enrolled", "alreadyPresent", "skippedInvalid", "skippedUnauthorized",
            "unresolvedIdentity", "staleEntitlement", "missing", "capacity", "retry", "deferred")
READS = dry.READS
WRITES = dry.WRITES


class RuntimeApplyMigrationTests(unittest.TestCase):
    """Real unchanged migration Lua, restricted to an isolated Unix-socket Redis."""

    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    setUp = manual.DiscoveryMigrationTests.setUp
    seed = manual.DiscoveryMigrationTests.seed
    index = manual.DiscoveryMigrationTests.index
    snapshot = manual.DiscoveryMigrationTests.snapshot
    transport = manual.DiscoveryMigrationTests.transport
    command_counts = manual.DiscoveryMigrationTests.command_counts
    fill_index = manual.DiscoveryMigrationTests.fill_index
    all_snapshot_keys = dry.RuntimeDiscoveryMigrationTests.all_snapshot_keys

    def apply(self, **overrides):
        options = {"owner_mailbox_id": http.MAILBOX_ID,
                   "owner_security_configuration": self.config,
                   "command_transport": self.transport}
        options.update(overrides)
        return migration.run_runtime_apply_page(self.context, (), **options)

    def assert_safe(self, result):
        self.assertEqual(set(result), set(COUNTERS) | {
            "v", "dryRun", "scanCalls", "hasMore", "done", "status", "error"})
        self.assertIs(result["dryRun"], False)
        self.assertEqual(result["v"], 1)
        self.assertLessEqual(result["scanCalls"], 1)
        self.assertLessEqual(result["examined"], 5)
        self.assertEqual(sum(result[name] for name in COUNTERS[1:]), result["examined"])
        self.assertEqual(result["done"], not result["hasMore"])
        encoded = json.dumps(result)
        for private in ("cuevion:", "collaborationId", "sourceRef", "owner@example.com", "bodyText",
                        "participants", "guest", "secret", "nextCursor", "wouldEnroll", "alreadyPlanned"):
            self.assertNotIn(private, encoded)
        for key in self.keys:
            self.assertNotIn(key.removeprefix(store.V2_THREAD_KEY_PREFIX), encoded)

    @contextlib.contextmanager
    def observed_lua_commands(self):
        """Inspect commands only on this test's private server; never log payloads."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(self.socket_path)
            stream = connection.makefile("rb")
            try:
                connection.sendall(b"*1\r\n$7\r\nMONITOR\r\n")
                self.assertEqual(self.client._read(stream), "OK")
                records = []
                yield records
                self.client.command(["ECHO", "runtime-apply-observation-finished"])
                while True:
                    line = self.client._read(stream)
                    if '"ECHO" "runtime-apply-observation-finished"' in line:
                        break
                    if "[0 lua] " in line:
                        arguments = re.findall(r'"((?:\\.|[^"\\])*)"', line.split("[0 lua] ", 1)[1])
                        records.append(arguments)
            finally:
                stream.close()

    def test_healthy_three_candidates_write_allowlist_expiries_and_idempotent_repeat(self):
        values = [self.seed(0, legacy=True, ttl=400_000),
                  self.seed(1, ttl=100_000, state="resolved", participants=[{
                      "userId": summary.PARTICIPANT, "displayName": "Participant", "membershipRef": "tinv_original"}]),
                  self.seed(2, ttl=250_000, state="needs_action")]
        foreign_keys = [store.build_v2_guest_session_key("a" * 64),
                        store.build_v2_invite_token_key("b" * 64),
                        store.build_v2_invite_key("I" * 22),
                        store.build_v2_external_guest_index_key(values[0]["collaborationId"])]
        for key in foreign_keys:
            self.client.command(["SET", key, "synthetic-private-guest-session-content", "PX", 200_000])
        keys = self.all_snapshot_keys(values) + foreign_keys
        before = self.snapshot(keys)
        counters_before = self.command_counts(("scan", "eval") + READS + WRITES)
        output = io.StringIO()
        with self.observed_lua_commands() as observed, contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(output), \
             patch.object(migration, "_save", side_effect=AssertionError("no checkpoint")), \
             patch.object(migration.os, "open", side_effect=AssertionError("no filesystem")):
            result = self.apply()
        counters_after = self.command_counts(counters_before)
        delta = {name: counters_after[name] - counters_before[name] for name in counters_before}
        self.assertEqual((result["examined"], result["enrolled"], result["scanCalls"], result["done"]),
                         (3, 3, 1, True), result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(output.getvalue(), "")
        after = self.snapshot(keys)
        # Only the proven legacy authority group and this recipient's index change.
        self.assertEqual(after[1:6], before[1:6])
        self.assertEqual(after[7:], before[7:])
        self.assertEqual(after[0][1], before[0][1])
        self.assertEqual(after[6][1], max(row[1] for row in before[:3]))
        enriched = store._load_v2_thread(values[0]["collaborationId"], command_transport=self.client.transport).record
        self.assertEqual({key: enriched[key] for key in values[0]}, values[0])
        self.assertEqual(enriched["ownerUserId"], self.member.user_id)
        self.assertEqual(enriched["ownerDisplayName"], self.member.name)
        self.assertEqual(enriched["participants"], [])
        self.assertLessEqual({command[0].lower() for command in observed},
                             set(READS) | {"hset", "pexpire", "set"})
        writes = [command for command in observed if command[0].lower() in WRITES]
        self.assertEqual([c[0] for c in writes], ["HSET", "PEXPIRE", "HSET", "HSET", "SET"])
        for command in writes[:-1]:
            self.assertEqual(command[1], self.index())
        self.assertEqual((writes[-1][1], writes[-1][-1]), (self.keys[0], "KEEPTTL"))
        self.assertEqual((delta["scan"], delta["eval"], delta["hset"], delta["pexpire"], delta["hdel"], delta["set"]),
                         (1, 2, 3, 1, 0, 1), delta)
        self.assertEqual(sum(delta[name] for name in WRITES if name not in {"hset", "pexpire", "set"}), 0)
        self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
        self.assertEqual(self.commands[0], ["SCAN", "0", "MATCH", migration.SCAN_PATTERN, "COUNT", 100])
        for command in self.commands[1:]:
            self.assertIs(json.loads(command[3 + command[2]])["dryRun"], False)
        self.assert_safe(result)
        print("Local isolated runtime three-candidate apply commands: " + json.dumps({
            "SCAN": delta["scan"], "EVAL": delta["eval"], "nestedReads": sum(delta[name] for name in READS),
            "readBreakdown": {name: delta[name] for name in READS}, "HSET": delta["hset"],
            "PEXPIRE": delta["pexpire"], "HDEL": delta["hdel"], "SET_KEEPTTL": delta["set"],
            "otherWrites": 0}, sort_keys=True))
        self.commands.clear()
        before_repeat = self.snapshot(keys)
        writes_before = self.command_counts(WRITES)
        with self.observed_lua_commands() as repeated:
            result = self.apply()
        self.assertEqual((result["alreadyPresent"], result["enrolled"], result["done"]), (3, 0, True), result)
        self.assertEqual(self.snapshot(keys), before_repeat)
        self.assertEqual(self.command_counts(WRITES), writes_before)
        self.assertLessEqual({command[0].lower() for command in repeated}, set(READS))
        self.assertEqual(self.client.command(["HLEN", self.index()]), 3)
        self.assert_safe(result)

    def test_fixed_apply_has_no_runtime_mode_budget_or_continuation_input(self):
        for key, value in (("dry_run", True), ("apply", False), ("cursor", "0"),
                           ("scan_budget", 10), ("page_size", 100), ("checkpoint_path", "/tmp/unused")):
            with self.subTest(field=key), self.assertRaises(TypeError):
                self.apply(**{key: value})
        self.assertEqual(self.commands, [])

    def test_overshoot_five_candidate_bound_and_empty_cursor_never_continue(self):
        values = [self.seed(i) for i in range(8)]
        self.scan_responses = [["123456789", self.keys + self.keys], ["0", self.keys]]
        before = self.snapshot(self.keys)
        result = self.apply()
        self.assertEqual((result["examined"], result["enrolled"], result["scanCalls"]), (5, 5, 1), result)
        self.assertTrue(result["hasMore"])
        self.assertEqual(self.snapshot(self.keys), before)
        self.assertEqual(len(self.scan_responses), 1)
        self.assertEqual(self.client.command(["HLEN", self.index()]), 5)
        for value in values[5:]:
            self.assertIsNone(self.client.command(["HGET", self.index(), value["collaborationId"]]))
        self.assert_safe(result)
        self.commands.clear()
        self.scan_responses = [["17", []], ["0", self.keys]]
        result = self.apply()
        self.assertEqual((result["examined"], result["hasMore"], result["scanCalls"]), (0, True, 1))
        self.assertEqual([c[0] for c in self.commands], ["SCAN"])
        self.assertEqual(len(self.scan_responses), 1)

    def test_malformed_unauthorized_stale_missing_and_bad_source_candidates_never_write(self):
        values = [self.seed(0), self.seed(1, ownerUserId=summary.OTHER, ownerEmail="other@example.com"),
                  self.seed(2, ownerUserId=summary.OTHER, ownerEmail="other@example.com", participants=[{
                      "userId": self.member.user_id, "displayName": "Owner Person", "membershipRef": "tinv_rebound"}]),
                  self.seed(3), self.seed(4)]
        self.client.command(["SET", self.keys[0], '{"private-content":"invalid"}', "KEEPTTL"])
        self.client.command(["DEL", self.keys[3]])
        pointer = store.build_v2_source_thread_key(values[4]["ownerEmail"], values[4]["mailboxId"], values[4]["sourceRef"])
        self.client.command(["SET", pointer, "Z" * 22, "KEEPTTL"])
        keys = self.all_snapshot_keys(values)
        before = self.snapshot(keys)
        writes_before = self.command_counts(WRITES)
        self.scan_responses = [["0", self.keys]]
        result = self.apply()
        self.assertEqual((result["skippedInvalid"], result["skippedUnauthorized"], result["staleEntitlement"],
                          result["missing"], result["enrolled"]), (2, 1, 1, 1, 0), result)
        self.assertEqual(self.snapshot(keys), before)
        self.assertEqual(self.command_counts(WRITES), writes_before)
        self.assert_safe(result)

    def test_canonical_digest_race_is_zero_migration_write_and_not_retried(self):
        value = self.seed(legacy=True)
        replacement = {**value, "state": "resolved", "updatedAt": value["updatedAt"] + 1}
        self.scan_responses = [["0", self.keys]]
        original = self.transport
        changed_snapshot = []
        def race(command):
            if command[0] == "EVAL" and command[1] == migration._ENROLL_LUA:
                self.client.command(["SET", self.keys[0], fixtures.wire_json(replacement, "thread"), "KEEPTTL"])
                changed_snapshot.extend(self.snapshot(self.all_snapshot_keys([value])))
            return original(command)
        with self.observed_lua_commands() as observed:
            result = self.apply(command_transport=race)
        self.assertEqual((result["retry"], result["enrolled"], result["hasMore"]), (1, 0, True), result)
        self.assertEqual(self.snapshot(self.all_snapshot_keys([value])), changed_snapshot)
        self.assertFalse([c for c in observed if c[0].lower() in WRITES])
        self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
        self.assert_safe(result)

    def test_capacity_does_not_evict_live_records_and_prunes_only_absent_reference(self):
        self.fill_index(1000)
        value = self.seed(1)
        existing_live = store.build_v2_thread_key(f"{10001:022d}")
        keys = self.all_snapshot_keys([value]) + [existing_live]
        before = self.snapshot(keys)
        writes_before = self.command_counts(WRITES)
        self.scan_responses = [["0", self.keys]]
        result = self.apply()
        self.assertEqual((result["capacity"], result["enrolled"], result["hasMore"]), (1, 0, True), result)
        self.assertEqual(self.snapshot(keys), before)
        self.assertEqual(self.command_counts(WRITES), writes_before)
        absent_id = f"{10000:022d}"
        self.client.command(["DEL", store.build_v2_thread_key(absent_id)])
        self.scan_responses = [["0", self.keys]]
        with self.observed_lua_commands() as observed:
            result = self.apply()
        self.assertEqual((result["enrolled"], result["done"]), (1, True), result)
        writes = [c for c in observed if c[0].lower() in WRITES]
        self.assertEqual([c[0] for c in writes], ["HDEL", "HSET"])
        self.assertEqual(writes[0], ["HDEL", self.index(), absent_id])
        self.assertEqual(writes[1][1], self.index())
        self.assertEqual(self.snapshot(keys[:-2]), before[:-2])
        self.assertEqual(self.snapshot([existing_live]), [before[-1]])
        self.assertEqual(self.client.command(["HLEN", self.index()]), 1000)
        self.assert_safe(result)

    def test_pre_scan_authority_and_configuration_failures_are_closed(self):
        for mailbox in (None, "", "other-mailbox", "Upper"):
            self.assertEqual(self.apply(owner_mailbox_id=mailbox)["error"]["code"], "mailbox_not_authorized")
        original_member = self.member
        self.member = replace(self.member, workspace_id="wsp_" + "Z" * 22)
        self.assertEqual(self.apply()["error"]["code"], "owner_not_authorized")
        self.member = original_member
        for authority in (patch.object(auth, "_resolve_current_authenticated_member", return_value=(None, "unavailable")),
                          patch.object(auth, "_resolve_active_team_member", return_value=(None, "unavailable"))):
            with authority:
                self.assertEqual(self.apply()["error"]["code"], "authority_unavailable")
        with patch.object(store, "resolve_v2_index_hmac_keys", return_value=None):
            self.assertEqual(self.apply()["error"]["code"], "migration_unavailable")
        self.mailbox.side_effect = lambda *a, **k: {"status": "unavailable"}
        self.assertEqual(self.apply()["error"]["code"], "authority_unavailable")
        self.assertEqual(self.commands, [])

    def test_current_participant_is_only_recipient_and_owner_needs_no_team_enrollment(self):
        value = self.seed(1, ownerEmail="other@example.com", ownerUserId=summary.OTHER, participants=[{
            "userId": self.member.user_id, "displayName": "Owner Person", "membershipRef": "tinv_original"}])
        before = self.snapshot(self.all_snapshot_keys([value])[:-1])
        self.scan_responses = [["0", self.keys]]
        self.assertEqual(self.apply()["enrolled"], 1)
        self.assertEqual(self.snapshot(self.all_snapshot_keys([value])[:-1]), before)
        self.assertEqual(self.client.command(["EXISTS", self.index(summary.OTHER)]), 0)
        self.seed(2)
        self.team.side_effect = lambda *a: (None, "not_active")
        self.scan_responses = [["0", [self.keys[-1]]]]
        self.assertEqual(self.apply()["enrolled"], 1)

    def test_byte_bound_defers_without_second_page_or_rewriting_deferred_candidates(self):
        values = [self.seed(i, legacy=True,
            sourceMessage={**summary.thread()["sourceMessage"], "bodyText": "b" * models.MAX_V2_SOURCE_BODY},
            messages=[fixtures.message_record(index=j, created_at=summary.thread()["updatedAt"], text="m" * 16000)
                      for j in range(7)]) for i in range(5)]
        keys = self.all_snapshot_keys(values)
        before = self.snapshot(keys)
        self.scan_responses = [["0", self.keys]]
        result = self.apply()
        self.assertEqual((result["examined"], result["enrolled"], result["deferred"], result["hasMore"]),
                         (5, 1, 4, True), result)
        after = self.snapshot(keys)
        self.assertEqual(after[1:-1], before[1:-1])
        self.assertEqual(after[0][1], before[0][1])
        self.assertEqual([c[0] for c in self.commands], ["SCAN", "EVAL", "EVAL"])
        self.assert_safe(result)

    def test_scan_eval_and_malformed_protocol_fail_safely_without_retry(self):
        self.seed()
        for failure_step in ("SCAN", "EVAL"):
            calls = []
            def failing(command):
                calls.append(command[0])
                if command[0] == failure_step:
                    raise RuntimeError("private-secret-content")
                return {"result": ["0", self.keys]}
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = self.apply(command_transport=failing)
            self.assertEqual(result["error"]["code"], "migration_unavailable")
            self.assertEqual(calls, ["SCAN"] if failure_step == "SCAN" else ["SCAN", "EVAL"])
            self.assertEqual(output.getvalue(), "")
            self.assert_safe(result)
        self.scan_responses = [["0", ["private-secret-key"]]]
        result = self.apply()
        self.assertEqual(result["error"]["code"], "migration_apply_failed")
        self.assert_safe(result)
