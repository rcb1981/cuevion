from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from . import owner_rate_limit as rate
from . import test_lua_redis_integration as fixtures
from . import test_owner_rate_limit as owners


USER_ID = "usr_" + "A" * 22
OTHER_USER_ID = "usr_" + "B" * 21 + "A"
MIGRATION_POLICY = rate.RATE_LIMIT_MIGRATION_DRY_RUN


class RuntimeMigrationRateLimitUnitTests(unittest.TestCase):
    def test_dedicated_policy_is_two_requests_and_one_refill_per_five_minutes(self):
        self.assertIn(MIGRATION_POLICY, rate.RATE_LIMIT_CLASSES)
        self.assertEqual(
            rate.owner_rate_limit_policy(MIGRATION_POLICY),
            rate.OwnerRateLimitPolicy("migration_dry_run", 300_000_000, 2),
        )

    def test_all_existing_policy_values_and_key_bytes_are_unchanged(self):
        expected = {
            rate.RATE_LIMIT_BOOTSTRAP: (5_000_000, 4),
            rate.RATE_LIMIT_READ: (500_000, 30),
            rate.RATE_LIMIT_WRITE: (2_000_000, 10),
            rate.RATE_LIMIT_OPERATOR_GRANT: (300_000_000, 3),
        }
        for policy, (interval, burst) in expected.items():
            with self.subTest(policy=policy):
                kwargs = (
                    {"operator_user_id": USER_ID}
                    if policy == rate.RATE_LIMIT_OPERATOR_GRANT else {}
                )
                digest = (
                    "134a52e8641cf57924c12ffb3627510674832a79a17c04e76b283c9060cf3135"
                    if policy == rate.RATE_LIMIT_OPERATOR_GRANT else
                    "eceaed8be40f6a47b22e1a1fcc0d355624e233f63fcd27efb6c9ee7aced8f58a"
                )
                self.assertEqual(
                    rate.owner_rate_limit_policy(policy),
                    rate.OwnerRateLimitPolicy(policy, interval, burst),
                )
                self.assertEqual(
                    rate.build_owner_rate_limit_key(
                        owners._context(), policy, owners._configuration(), **kwargs,
                    ),
                    f"cuevion:collab:v2:{{cuevion-collab-v2}}:owner-rate:{policy}:{digest}",
                )

    def test_key_binds_canonical_user_workspace_with_its_own_hmac_domain(self):
        configuration = owners._configuration()

        def key(context=None, user_id=USER_ID, policy=MIGRATION_POLICY):
            return rate.build_owner_rate_limit_key(
                context or owners._context(), policy, configuration,
                operator_user_id=user_id,
            )

        expected = key()
        self.assertIsNotNone(expected)
        self.assertEqual(
            key(owners._context(
                owner_email="rotated@example.com", subject="auth0|new-session",
                session_id=owners._b64(b"t" * 32),
            )),
            expected,
        )
        self.assertIsNotNone(key(user_id=OTHER_USER_ID))
        self.assertNotEqual(key(user_id=OTHER_USER_ID), expected)
        self.assertNotEqual(
            key(owners._context(workspace_id="wsp_" + "B" * 22)), expected,
        )
        self.assertNotEqual(
            key(policy=rate.RATE_LIMIT_OPERATOR_GRANT).rsplit(":", 1)[1],
            expected.rsplit(":", 1)[1],
        )
        for private in (USER_ID, owners.OWNER_EMAIL, owners.WORKSPACE_ID):
            self.assertNotIn(private, expected)

    def test_invalid_or_missing_canonical_user_is_closed_before_eval(self):
        transport = Mock()
        for user_id in (None, "", "auth0|external", "usr_short", " " + USER_ID, 1):
            with self.subTest(user_id=user_id):
                result = rate.consume_owner_rate_limit(
                    owners._context(), MIGRATION_POLICY, owners._configuration(),
                    operator_user_id=user_id, command_transport=transport,
                )
                self.assertEqual(result, rate.OwnerRateLimitDecision("unavailable"))
        transport.assert_not_called()

    def test_existing_email_policies_still_reject_operator_identity(self):
        transport = Mock()
        for policy in (rate.RATE_LIMIT_BOOTSTRAP, rate.RATE_LIMIT_READ, rate.RATE_LIMIT_WRITE):
            result = rate.consume_owner_rate_limit(
                owners._context(), policy, owners._configuration(),
                operator_user_id=USER_ID, command_transport=transport,
            )
            self.assertEqual(result, rate.OwnerRateLimitDecision("unavailable"))
        transport.assert_not_called()

    def test_long_policy_retry_is_validated_then_keeps_existing_http_contract(self):
        for policy in (MIGRATION_POLICY, rate.RATE_LIMIT_OPERATOR_GRANT):
            for supplied, expected in (
                ("1", rate.OwnerRateLimitDecision("limited", 1)),
                ("60", rate.OwnerRateLimitDecision("limited", 60)),
                ("61", rate.OwnerRateLimitDecision("limited", 60)),
                ("300", rate.OwnerRateLimitDecision("limited", 60)),
                ("301", rate.OwnerRateLimitDecision("unavailable")),
                ("0", rate.OwnerRateLimitDecision("unavailable")),
                ("0300", rate.OwnerRateLimitDecision("unavailable")),
                (300, rate.OwnerRateLimitDecision("unavailable")),
            ):
                with self.subTest(policy=policy, supplied=supplied):
                    result = rate.consume_owner_rate_limit(
                        owners._context(), policy, owners._configuration(),
                        operator_user_id=USER_ID,
                        command_transport=lambda _command: {
                            "result": json.dumps({"status": "limited", "retryAfter": supplied}),
                        },
                    )
                    self.assertEqual(result, expected)

    def test_existing_email_policy_retry_validation_remains_unchanged(self):
        for policy in (rate.RATE_LIMIT_BOOTSTRAP, rate.RATE_LIMIT_READ, rate.RATE_LIMIT_WRITE):
            for supplied, expected in (
                ("60", rate.OwnerRateLimitDecision("limited", 60)),
                ("61", rate.OwnerRateLimitDecision("unavailable")),
                ("300", rate.OwnerRateLimitDecision("unavailable")),
            ):
                with self.subTest(policy=policy, supplied=supplied):
                    result = rate.consume_owner_rate_limit(
                        owners._context(), policy, owners._configuration(),
                        command_transport=lambda _command: {
                            "result": json.dumps({"status": "limited", "retryAfter": supplied}),
                        },
                    )
                    self.assertEqual(result, expected)


class RuntimeMigrationRateLimitRedisTests(unittest.TestCase):
    """Run the unchanged limiter Lua against an isolated Unix-socket Redis."""

    @classmethod
    def setUpClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixtures.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])
        self.context = owners._context()
        self.configuration = owners._configuration()

    def consume(self, policy=MIGRATION_POLICY, user_id=USER_ID):
        return rate.consume_owner_rate_limit(
            self.context, policy, self.configuration,
            operator_user_id=(
                user_id if policy in {MIGRATION_POLICY, rate.RATE_LIMIT_OPERATOR_GRANT} else None
            ),
            command_transport=self.client.transport,
        )

    def migration_key(self):
        return rate.build_owner_rate_limit_key(
            self.context, MIGRATION_POLICY, self.configuration,
            operator_user_id=USER_ID,
        )

    def test_two_allowed_then_limited_does_not_refresh_counter_or_expiry(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        key = self.migration_key()
        record = self.client.command(["GET", key])
        expiry = self.client.command(["PEXPIRETIME", key])
        ttl = self.client.command(["PTTL", key])
        self.assertGreater(ttl, 599_000)
        self.assertLessEqual(ttl, 601_000)
        for _ in range(3):
            self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
            self.assertEqual(self.client.command(["GET", key]), record)
            self.assertEqual(self.client.command(["PEXPIRETIME", key]), expiry)

    def test_one_refill_interval_restores_exactly_one_request(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        key = self.migration_key()
        record = json.loads(self.client.command(["GET", key]))
        # Seed the equivalent state after one five-minute refill, without waiting.
        record["tatUs"] = str(int(record["tatUs"]) - 300_000_000)
        seconds, micros = self.client.command(["TIME"])
        now = int(seconds) * 1_000_000 + int(micros)
        ttl = max(1, (int(record["tatUs"]) - now + 999) // 1_000) + 1_000
        self.client.command(["SET", key, json.dumps(record, separators=(",", ":")), "PX", ttl])
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))

    def test_exhausted_runtime_counter_preserves_all_existing_policy_bursts(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        for policy, burst in (
            (rate.RATE_LIMIT_BOOTSTRAP, 4), (rate.RATE_LIMIT_READ, 30),
            (rate.RATE_LIMIT_WRITE, 10), (rate.RATE_LIMIT_OPERATOR_GRANT, 3),
        ):
            with self.subTest(policy=policy):
                for _ in range(burst):
                    self.assertEqual(self.consume(policy), rate.OwnerRateLimitDecision("allowed"))
                result = self.consume(policy)
                self.assertEqual(result.status, "limited")
                self.assertGreaterEqual(result.retry_after_seconds, 1)
                self.assertLessEqual(result.retry_after_seconds, 60)
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        self.assertEqual(self.consume(user_id=OTHER_USER_ID), rate.OwnerRateLimitDecision("allowed"))


if __name__ == "__main__":
    unittest.main()
