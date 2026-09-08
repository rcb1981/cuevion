from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from . import owner_rate_limit as rate
from . import test_lua_redis_integration as fixtures
from . import test_owner_rate_limit as owners


USER_ID = "usr_" + "A" * 22
OTHER_USER_ID = "usr_" + "B" * 21 + "A"
APPLY_POLICY = rate.RATE_LIMIT_MIGRATION_APPLY
CANONICAL_POLICIES = {APPLY_POLICY, rate.RATE_LIMIT_OPERATOR_GRANT, rate.RATE_LIMIT_MIGRATION_DRY_RUN}
OLD_POLICIES = {
    rate.RATE_LIMIT_BOOTSTRAP: (5_000_000, 4, 60),
    rate.RATE_LIMIT_READ: (500_000, 30, 60),
    rate.RATE_LIMIT_WRITE: (2_000_000, 10, 60),
    rate.RATE_LIMIT_OPERATOR_GRANT: (300_000_000, 3, 300),
    rate.RATE_LIMIT_MIGRATION_DRY_RUN: (300_000_000, 2, 300),
}


class RuntimeMigrationApplyRateLimitUnitTests(unittest.TestCase):
    def test_apply_policy_is_one_request_per_fifteen_minutes(self):
        self.assertIn(APPLY_POLICY, rate.RATE_LIMIT_CLASSES)
        self.assertEqual(
            rate.owner_rate_limit_policy(APPLY_POLICY),
            rate.OwnerRateLimitPolicy("migration_apply", 900_000_000, 1),
        )

    def test_all_existing_policy_values_and_key_bytes_are_unchanged(self):
        digests = {
            rate.RATE_LIMIT_OPERATOR_GRANT:
                "134a52e8641cf57924c12ffb3627510674832a79a17c04e76b283c9060cf3135",
            rate.RATE_LIMIT_MIGRATION_DRY_RUN:
                "2e29c477c2078def0e96b56db7d658b1489b64adbf6772b0da496f85da4cfd16",
        }
        for policy, (interval, burst, _) in OLD_POLICIES.items():
            with self.subTest(policy=policy):
                self.assertEqual(
                    rate.owner_rate_limit_policy(policy), rate.OwnerRateLimitPolicy(policy, interval, burst),
                )
                digest = digests.get(policy, "eceaed8be40f6a47b22e1a1fcc0d355624e233f63fcd27efb6c9ee7aced8f58a")
                self.assertEqual(
                    rate.build_owner_rate_limit_key(
                        owners._context(), policy, owners._configuration(),
                        operator_user_id=USER_ID if policy in CANONICAL_POLICIES else None,
                    ),
                    f"cuevion:collab:v2:{{cuevion-collab-v2}}:owner-rate:{policy}:{digest}",
                )

    def test_apply_key_uses_current_user_workspace_and_separate_purpose_domain(self):
        configuration = owners._configuration()

        def key(*, user_id=USER_ID, context=None, policy=APPLY_POLICY):
            return rate.build_owner_rate_limit_key(
                context or owners._context(), policy, configuration, operator_user_id=user_id,
            )

        original = key()
        self.assertIsNotNone(original)
        self.assertEqual(key(context=owners._context(
            owner_email="changed@example.com", subject="auth0|rotated-subject",
            session_id=owners._b64(b"t" * 32),
        )), original)
        self.assertIsNotNone(key(user_id=OTHER_USER_ID))
        self.assertNotEqual(key(user_id=OTHER_USER_ID), original)
        self.assertNotEqual(key(context=owners._context(workspace_id="wsp_" + "B" * 22)), original)
        for policy in (rate.RATE_LIMIT_OPERATOR_GRANT, rate.RATE_LIMIT_MIGRATION_DRY_RUN):
            self.assertNotEqual(key(policy=policy).rsplit(":", 1)[1], original.rsplit(":", 1)[1])
        for private in (USER_ID, owners.OWNER_EMAIL, owners.WORKSPACE_ID):
            self.assertNotIn(private, original)

    def test_missing_noncanonical_user_or_unverified_context_never_contacts_redis(self):
        transport = Mock()
        for user_id in (None, "", "auth0|subject", "usr_short", "usr_" + "B" * 22, " " + USER_ID, True):
            result = rate.consume_owner_rate_limit(
                owners._context(), APPLY_POLICY, owners._configuration(),
                operator_user_id=user_id, command_transport=transport,
            )
            self.assertEqual(result, rate.OwnerRateLimitDecision("unavailable"))
        result = rate.consume_owner_rate_limit(
            {"owner_email": owners.OWNER_EMAIL, "workspace_id": owners.WORKSPACE_ID},
            APPLY_POLICY, owners._configuration(), operator_user_id=USER_ID, command_transport=transport,
        )
        self.assertEqual(result, rate.OwnerRateLimitDecision("unavailable"))
        transport.assert_not_called()

    def test_only_apply_accepts_retry_up_to_nine_hundred_seconds(self):
        for policy, maximum in [(APPLY_POLICY, 900)] + [(name, spec[2]) for name, spec in OLD_POLICIES.items()]:
            for supplied, expected in (
                ("1", rate.OwnerRateLimitDecision("limited", 1)),
                (str(maximum), rate.OwnerRateLimitDecision("limited", 60)),
                (str(maximum + 1), rate.OwnerRateLimitDecision("unavailable")),
                ("0", rate.OwnerRateLimitDecision("unavailable")),
                ("0900", rate.OwnerRateLimitDecision("unavailable")),
                (900, rate.OwnerRateLimitDecision("unavailable")),
            ):
                with self.subTest(policy=policy, supplied=supplied):
                    result = rate.consume_owner_rate_limit(
                        owners._context(), policy, owners._configuration(),
                        operator_user_id=USER_ID if policy in CANONICAL_POLICIES else None,
                        command_transport=lambda _command: {
                            "result": json.dumps({"status": "limited", "retryAfter": supplied}),
                        },
                    )
                    self.assertEqual(result, expected)

    def test_one_existing_counter_eval_has_no_identity_or_secrets_in_arguments(self):
        transport = Mock(return_value={"result": '{"status":"allowed"}'})
        result = rate.consume_owner_rate_limit(
            owners._context(), APPLY_POLICY, owners._configuration(),
            operator_user_id=USER_ID, command_transport=transport,
        )
        self.assertEqual(result, rate.OwnerRateLimitDecision("allowed"))
        transport.assert_called_once()
        command = transport.call_args.args[0]
        self.assertEqual(command[:3], ["EVAL", rate._OWNER_RATE_LIMIT_LUA, 1])
        self.assertEqual(command[4:], ["900000000", "1", "128"])
        self.assertIn(":owner-rate:migration_apply:", command[3])
        for private in (USER_ID, owners.OWNER_EMAIL, owners.WORKSPACE_ID, owners._b64(owners.RATE_KEY)):
            self.assertNotIn(private, json.dumps(command))


class RuntimeMigrationApplyRateLimitRedisTests(unittest.TestCase):
    """Exercise only security counters on a private Unix-socket Redis fixture."""

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

    def consume(self, policy=APPLY_POLICY, user_id=USER_ID):
        return rate.consume_owner_rate_limit(
            self.context, policy, self.configuration,
            operator_user_id=user_id if policy in CANONICAL_POLICIES else None,
            command_transport=self.client.transport,
        )

    def key(self):
        return rate.build_owner_rate_limit_key(
            self.context, APPLY_POLICY, self.configuration, operator_user_id=USER_ID,
        )

    def test_one_allowed_then_denied_preserves_record_and_absolute_expiry(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        key = self.key()
        record = self.client.command(["GET", key])
        expiry = self.client.command(["PEXPIRETIME", key])
        ttl = self.client.command(["PTTL", key])
        self.assertGreater(ttl, 899_000)
        self.assertLessEqual(ttl, 901_000)
        for _ in range(3):
            self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
            self.assertEqual(self.client.command(["GET", key]), record)
            self.assertEqual(self.client.command(["PEXPIRETIME", key]), expiry)

    def test_full_fifteen_minute_refill_is_required_and_restores_one_request(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        key = self.key()

        def advance_fixture_counter(seconds):
            # Seed equivalent elapsed-time state; the real limiter still uses Redis TIME.
            record = json.loads(self.client.command(["GET", key]))
            record["tatUs"] = str(int(record["tatUs"]) - seconds * 1_000_000)
            redis_seconds, micros = self.client.command(["TIME"])
            now = int(redis_seconds) * 1_000_000 + int(micros)
            ttl = max(1, (int(record["tatUs"]) - now + 999) // 1_000) + 1_000
            self.client.command(["SET", key, json.dumps(record, separators=(",", ":")), "PX", ttl])

        advance_fixture_counter(840)
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        advance_fixture_counter(60)
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))

    def test_exhausted_apply_counter_preserves_every_existing_policy_burst(self):
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("allowed"))
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        for policy, (_, burst, _) in OLD_POLICIES.items():
            with self.subTest(policy=policy):
                for _ in range(burst):
                    self.assertEqual(self.consume(policy), rate.OwnerRateLimitDecision("allowed"))
                decision = self.consume(policy)
                self.assertEqual(decision.status, "limited")
                self.assertGreaterEqual(decision.retry_after_seconds, 1)
                self.assertLessEqual(decision.retry_after_seconds, 60)
        self.assertEqual(self.consume(), rate.OwnerRateLimitDecision("limited", 60))
        self.assertEqual(self.consume(user_id=OTHER_USER_ID), rate.OwnerRateLimitDecision("allowed"))


if __name__ == "__main__":
    unittest.main()
