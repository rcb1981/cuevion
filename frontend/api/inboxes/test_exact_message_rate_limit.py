from __future__ import annotations

import base64
import json
import unittest

from api.auth.runtime import AuthenticatedMemberContext
from api.collaboration import owner_rate_limit as engine
from api.collaboration import test_lua_redis_integration as redis_tests
from . import exact_message_rate_limit as rate


ENV = {engine.RATE_LIMIT_HMAC_ENV: base64.urlsafe_b64encode(b"c3d0-local-rate-key-32-byte-value!").rstrip(b"=").decode()}


def member(user="A" * 22, workspace="W" * 22):
    return AuthenticatedMemberContext("usr_" + user, "owner@example.com", "Owner", "wsp_" + workspace, "owner")


class ExactMessageRateLimitTests(unittest.TestCase):
    def test_policy_and_scope_domain_separation(self):
        self.assertEqual((rate.POLICY.emission_interval_microseconds, rate.POLICY.burst), (2_000_000, 10))
        config = engine.parse_owner_rate_limit_configuration(ENV)
        keys = {rate.build_rate_limit_key(value, config) for value in (member(), member("B" * 21 + "A"), member(workspace="X" * 22))}
        self.assertEqual(len(keys), 3)
        for key in keys:
            self.assertIn(":inbox-exact-rate:", key)
            self.assertNotIn("owner@example.com", key)
            self.assertNotIn("notification-rate", key)
            self.assertNotIn("usr_", key)
            self.assertNotIn("wsp_", key)

    def test_one_eval_and_fail_closed_validation(self):
        commands = []
        result = rate.consume_exact_message_rate_limit(member(), environment=ENV, command_transport=lambda c: commands.append(c) or {"result": json.dumps({"status": "allowed"})})
        self.assertEqual(result.status, "allowed")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][4:], ["2000000", "10", "128"])
        for payload in ({"status": "allowed", "secret": "x"}, {"status": "limited", "retryAfter": "00"}, {"status": "limited", "retryAfter": 2}, {"status": "unavailable"}):
            self.assertEqual(rate.consume_exact_message_rate_limit(member(), environment=ENV, command_transport=lambda _c: {"result": json.dumps(payload)}).status, "unavailable")
        self.assertEqual(rate.consume_exact_message_rate_limit(member(), environment={}).status, "unavailable")


class ExactMessageRateLimitRedisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        redis_tests.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        redis_tests.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])

    def test_burst_ten_and_independent_workspace(self):
        for _ in range(10):
            self.assertEqual(rate.consume_exact_message_rate_limit(member(), environment=ENV, command_transport=self.client.transport).status, "allowed")
        self.assertEqual(rate.consume_exact_message_rate_limit(member(), environment=ENV, command_transport=self.client.transport).status, "limited")
        self.assertEqual(rate.consume_exact_message_rate_limit(member(workspace="X" * 22), environment=ENV, command_transport=self.client.transport).status, "allowed")

    def test_local_command_cost(self):
        costs = []
        for _ in range(2):
            self.client.command(["CONFIG", "RESETSTAT"])
            self.assertEqual(rate.consume_exact_message_rate_limit(member(), environment=ENV, command_transport=self.client.transport).status, "allowed")
            stats = self.client.command(["INFO", "commandstats"])
            counts = {}
            for line in stats.splitlines():
                if line.startswith("cmdstat_"):
                    name, values = line.split(":", 1)
                    if name not in {"cmdstat_config|resetstat", "cmdstat_info"}:
                        counts[name.removeprefix("cmdstat_")] = int(values.split(",", 1)[0].split("=", 1)[1])
            costs.append(counts)
        self.assertEqual(costs, [{"eval": 1, "time": 1, "get": 1, "set": 1}, {"eval": 1, "time": 1, "get": 1, "pttl": 1, "set": 1}])


if __name__ == "__main__":
    unittest.main()
