from __future__ import annotations

import base64
import json
import unittest

from api.auth.runtime import AuthenticatedMemberContext
from api.collaboration import owner_rate_limit as existing
from api.collaboration import test_lua_redis_integration as shared_redis_tests
from . import rate_limit


KEY = base64.urlsafe_b64encode(b"notification-rate-key-32-bytes-0001").rstrip(b"=").decode()
ENV = {existing.RATE_LIMIT_HMAC_ENV: KEY}
WORKSPACE = "wsp_" + "W" * 22
USER = "usr_" + "A" * 22


def member(**updates):
    values = dict(user_id=USER, email="private@example.com", name="Current user",
                  workspace_id=WORKSPACE, membership_role="owner")
    values.update(updates)
    return AuthenticatedMemberContext(**values)


class NotificationRateLimitTests(unittest.TestCase):
    def test_exact_policies_and_domain_separation(self):
        config = existing.parse_owner_rate_limit_configuration(ENV)
        keys = set()
        for operation, interval, burst in (("summary", 1_000_000, 20),
                                           ("list", 1_000_000, 20),
                                           ("mark_read", 2_000_000, 10)):
            policy = rate_limit.POLICIES[operation]
            self.assertEqual((policy.emission_interval_microseconds, policy.burst), (interval, burst))
            for identity in (member(), member(user_id="usr_" + "B" * 21 + "A"),
                             member(workspace_id="wsp_" + "X" * 22)):
                key = rate_limit.build_notification_rate_limit_key(identity, operation, config)
                keys.add(key)
                self.assertIn("{cuevion-collab-v2}", key)
                for private in (identity.user_id, identity.workspace_id, identity.email):
                    self.assertNotIn(private, key)
                self.assertNotIn("owner-rate", key)
                self.assertNotIn("migration", key)
        self.assertEqual(len(keys), 9)
        self.assertEqual(
            rate_limit.build_notification_rate_limit_key(member(), "summary", config),
            rate_limit.build_notification_rate_limit_key(member(email="changed@example.com"), "summary", config),
        )

    def test_single_eval_reuses_existing_algorithm_with_own_key(self):
        commands = []
        def transport(command):
            commands.append(command)
            return {"result": json.dumps({"status": "allowed"})}
        result = rate_limit.consume_notification_rate_limit(
            member(), "summary", environment=ENV, command_transport=transport,
        )
        self.assertEqual(result.status, "allowed")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ["EVAL", existing._OWNER_RATE_LIMIT_LUA, 1])
        self.assertEqual(commands[0][4:], ["1000000", "20", "128"])

    def test_bad_configuration_identity_operation_and_redis_fail_closed(self):
        for environment in ({}, {existing.RATE_LIMIT_HMAC_ENV: "bad"},
                            {**ENV, "CUEVION_COLLAB_INDEX_HMAC_KEY": KEY}):
            commands = []
            result = rate_limit.consume_notification_rate_limit(
                member(), "summary", environment=environment,
                command_transport=lambda c: commands.append(c),
            )
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(commands, [])
        for payload in ({"status": "malformed"}, {"status": "unavailable"},
                        {"status": "limited", "retryAfter": "00"},
                        {"status": "limited", "retryAfter": 2},
                        {"status": "allowed", "extra": "secret"}):
            result = rate_limit.consume_notification_rate_limit(
                member(), "list", environment=ENV,
                command_transport=lambda _c: {"result": json.dumps(payload)},
            )
            self.assertEqual(result.status, "unavailable")
        config = existing.parse_owner_rate_limit_configuration(ENV)
        for identity, operation in ((member(user_id="guest"), "summary"),
                                    (member(), "operator_grant"), (object(), "list")):
            self.assertIsNone(rate_limit.build_notification_rate_limit_key(identity, operation, config))


class NotificationRateLimitRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shared_redis_tests.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        shared_redis_tests.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        # This test class owns its isolated, socket-only Redis instance.
        self.client.command(["FLUSHALL"])

    def test_real_burst_is_bounded_and_operations_do_not_share_debt(self):
        for operation, burst in (("summary", 20), ("list", 20), ("mark_read", 10)):
            for _ in range(burst):
                self.assertEqual(rate_limit.consume_notification_rate_limit(
                    member(), operation, environment=ENV,
                    command_transport=self.client.transport,
                ).status, "allowed")
            result = rate_limit.consume_notification_rate_limit(
                member(), operation, environment=ENV, command_transport=self.client.transport,
            )
            self.assertEqual(result.status, "limited")
            self.assertIn(result.retry_after_seconds, {1, 2})
        self.assertEqual(rate_limit.consume_notification_rate_limit(
            member(user_id="usr_" + "B" * 21 + "A"), "summary", environment=ENV,
            command_transport=self.client.transport,
        ).status, "allowed")

    def test_real_malformed_rate_store_fails_closed(self):
        config = existing.parse_owner_rate_limit_configuration(ENV)
        key = rate_limit.build_notification_rate_limit_key(member(), "summary", config)
        self.client.command(["HSET", key, "field", "private"])
        result = rate_limit.consume_notification_rate_limit(
            member(), "summary", environment=ENV, command_transport=self.client.transport,
        )
        self.assertEqual(result.status, "unavailable")

    def test_real_redis_command_cost_first_and_existing_bucket(self):
        costs = []
        for _ in range(2):
            self.client.command(["CONFIG", "RESETSTAT"])
            self.assertEqual(rate_limit.consume_notification_rate_limit(
                member(), "summary", environment=ENV, command_transport=self.client.transport,
            ).status, "allowed")
            stats = self.client.command(["INFO", "commandstats"])
            counts = {}
            for line in stats.splitlines():
                if line.startswith("cmdstat_"):
                    name, values = line.split(":", 1)
                    count = int(values.split(",", 1)[0].split("=", 1)[1])
                    if name not in {"cmdstat_config|resetstat", "cmdstat_info"}:
                        counts[name.removeprefix("cmdstat_")] = count
            costs.append(counts)
        self.assertEqual(costs[0], {"eval": 1, "time": 1, "get": 1, "set": 1})
        self.assertEqual(costs[1], {"eval": 1, "time": 1, "get": 1, "pttl": 1, "set": 1})
