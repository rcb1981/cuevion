from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from . import owner_rate_limit
from .test_owner_rate_limit import RATE_KEY, WORKSPACE_ID, _configuration, _context


USER_ID = "usr_" + "u" * 21 + "A"
OTHER_USER_ID = "usr_" + "v" * 21 + "Q"
GRANT_CLASS = owner_rate_limit.RATE_LIMIT_OPERATOR_GRANT


class OperatorGrantRateLimitUnitTests(unittest.TestCase):
    def _key(self, *, user_id=USER_ID, context=None, rate_class=GRANT_CLASS):
        return owner_rate_limit.build_owner_rate_limit_key(
            _context() if context is None else context,
            rate_class,
            _configuration(),
            operator_user_id=user_id,
        )

    def test_policy_is_three_burst_and_one_grant_per_five_minutes(self):
        policy = owner_rate_limit.owner_rate_limit_policy(GRANT_CLASS)
        self.assertEqual(
            policy,
            owner_rate_limit.OwnerRateLimitPolicy(GRANT_CLASS, 300_000_000, 3),
        )
        self.assertIn(GRANT_CLASS, owner_rate_limit.RATE_LIMIT_CLASSES)

    def test_key_uses_canonical_user_workspace_and_separate_purpose(self):
        expected_identity = json.dumps(
            {
                "domain": "cuevion-collaboration-v2/operator-grant-rate-limit-key/v1",
                "userId": USER_ID,
                "workspaceId": WORKSPACE_ID,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        digest = hmac.new(RATE_KEY, expected_identity, hashlib.sha256).hexdigest()
        key = self._key()
        self.assertEqual(key, f"{owner_rate_limit.V2_KEY_PREFIX}:owner-rate:operator_grant:{digest}")
        for value in (USER_ID, WORKSPACE_ID, _context().owner_email):
            self.assertNotIn(value, key)

    def test_email_and_session_changes_do_not_reset_canonical_user_bucket(self):
        self.assertEqual(
            self._key(),
            self._key(context=_context(
                owner_email="changed@example.com",
                subject="auth0|changed-subject",
                session_id="t" * 32,
            )),
        )

    def test_different_user_or_workspace_has_a_separate_bucket(self):
        self.assertNotEqual(self._key(), self._key(user_id=OTHER_USER_ID))
        self.assertNotEqual(
            self._key(),
            self._key(context=_context(workspace_id="wsp_" + "x" * 22)),
        )

    def test_strict_user_id_and_real_owner_context_are_required(self):
        class UserString(str):
            pass

        for value in (
            None, "", "owner@example.com", "auth0|subject", "usr_short",
            "usr_" + "u" * 22, USER_ID + "=", USER_ID + "\n", True, 3,
            UserString(USER_ID),
        ):
            with self.subTest(value=value):
                self.assertIsNone(self._key(user_id=value))
        self.assertIsNone(self._key(context={"workspace_id": WORKSPACE_ID}))

    def test_invalid_operator_identity_is_denied_before_transport(self):
        commands = []
        decision = owner_rate_limit.consume_owner_rate_limit(
            _context(), GRANT_CLASS, _configuration(),
            command_transport=lambda command: commands.append(command),
            operator_user_id="owner@example.com",
        )
        self.assertEqual(decision.status, "unavailable")
        self.assertEqual(commands, [])

    def test_existing_policies_keys_and_omission_behavior_are_unchanged(self):
        identity = json.dumps(
            {
                "domain": "cuevion-collaboration-v2/owner-rate-limit-key/v1",
                "ownerEmail": _context().owner_email,
                "workspaceId": WORKSPACE_ID,
            },
            allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("ascii")
        digest = hmac.new(RATE_KEY, identity, hashlib.sha256).hexdigest()
        expected_policies = {"bootstrap": (5_000_000, 4), "read": (500_000, 30), "write": (2_000_000, 10)}
        for rate_class, (interval, burst) in expected_policies.items():
            with self.subTest(rate_class=rate_class):
                self.assertEqual(
                    owner_rate_limit.owner_rate_limit_policy(rate_class),
                    owner_rate_limit.OwnerRateLimitPolicy(rate_class, interval, burst),
                )
                self.assertEqual(
                    owner_rate_limit.build_owner_rate_limit_key(
                        _context(), rate_class, _configuration(),
                    ),
                    f"{owner_rate_limit.V2_KEY_PREFIX}:owner-rate:{rate_class}:{digest}",
                )
                self.assertIsNone(self._key(rate_class=rate_class))

    def test_existing_policy_cannot_accept_operator_identity(self):
        commands = []
        for rate_class in ("bootstrap", "read", "write"):
            result = owner_rate_limit.consume_owner_rate_limit(
                _context(), rate_class, _configuration(),
                operator_user_id=USER_ID,
                command_transport=lambda command: commands.append(command),
            )
            self.assertEqual(result.status, "unavailable")
        self.assertEqual(commands, [])

    def test_existing_lua_receives_only_hmac_key_and_conservative_policy(self):
        commands = []

        def transport(command):
            commands.append(command)
            return {"result": '{"status":"allowed"}'}

        decision = owner_rate_limit.consume_owner_rate_limit(
            _context(), GRANT_CLASS, _configuration(),
            operator_user_id=USER_ID, command_transport=transport,
        )
        self.assertEqual(decision.status, "allowed")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "EVAL")
        self.assertEqual(commands[0][1], owner_rate_limit._OWNER_RATE_LIMIT_LUA)
        self.assertEqual(commands[0][2:], [1, self._key(), "300000000", "3", "128"])
        self.assertNotIn(USER_ID, json.dumps(commands))
        self.assertNotIn("redis.call, 'SCAN'", commands[0][1])
        self.assertNotIn("redis.call, 'KEYS'", commands[0][1])

    def test_operator_retry_hint_clamps_without_changing_existing_contract(self):
        for seconds, expected in (("1", 1), ("60", 60), ("61", 60), ("300", 60)):
            result = owner_rate_limit.consume_owner_rate_limit(
                _context(), GRANT_CLASS, _configuration(), operator_user_id=USER_ID,
                command_transport=lambda _command: {"result": json.dumps({"status": "limited", "retryAfter": seconds})},
            )
            self.assertEqual(result, owner_rate_limit.OwnerRateLimitDecision("limited", expected))
        for rate_class in ("bootstrap", "read", "write"):
            result = owner_rate_limit.consume_owner_rate_limit(
                _context(), rate_class, _configuration(),
                command_transport=lambda _command: {"result": '{"status":"limited","retryAfter":"61"}'},
            )
            self.assertEqual(result.status, "unavailable")

    def test_operator_malformed_or_excessive_retry_response_fails_closed(self):
        for value in ("0", "301", "900", "01", "-1", "1.0", 3, None):
            with self.subTest(value=value):
                result = owner_rate_limit.consume_owner_rate_limit(
                    _context(), GRANT_CLASS, _configuration(), operator_user_id=USER_ID,
                    command_transport=lambda _command: {"result": json.dumps({"status": "limited", "retryAfter": value})},
                )
                self.assertEqual(result.status, "unavailable")


class OperatorGrantRateLimitRedisTests(unittest.TestCase):
    """Only a disposable, nonpersistent local Redis over a private Unix socket."""

    @classmethod
    def setUpClass(cls):
        from . import test_lua_redis_integration as redis_fixture

        cls.fixture = redis_fixture
        redis_fixture.ProductionLuaRedisIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.ProductionLuaRedisIntegrationTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.client.command(["FLUSHALL"])

    def _consume(self, *, user_id=USER_ID, context=None):
        return owner_rate_limit.consume_owner_rate_limit(
            _context() if context is None else context,
            GRANT_CLASS, _configuration(), operator_user_id=user_id,
            command_transport=self.client.transport,
        )

    def test_real_redis_burst_limit_canonical_identity_and_existing_class_isolation(self):
        self.assertEqual([self._consume().status for _ in range(3)], ["allowed"] * 3)
        limited = self._consume()
        self.assertEqual(limited, owner_rate_limit.OwnerRateLimitDecision("limited", 60))
        self.assertEqual(self._consume(context=_context(owner_email="new@example.com")).status, "limited")
        self.assertEqual(self._consume(user_id=OTHER_USER_ID).status, "allowed")
        self.assertEqual(self._consume(context=_context(workspace_id="wsp_" + "x" * 22)).status, "allowed")
        existing = owner_rate_limit.consume_owner_rate_limit(
            _context(), "read", _configuration(), command_transport=self.client.transport,
        )
        self.assertEqual(existing.status, "allowed")
        key = owner_rate_limit.build_owner_rate_limit_key(
            _context(), GRANT_CLASS, _configuration(), operator_user_id=USER_ID,
        )
        ttl = self.client.command(["PTTL", key])
        self.assertGreater(ttl, 890_000)
        self.assertLessEqual(ttl, 901_000)
        state = json.loads(self.client.command(["GET", key]))
        self.assertEqual(set(state), {"v", "tatUs"})

    def test_real_redis_concurrency_accepts_exactly_three(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(pool.map(lambda _index: self._consume(), range(24)))
        self.assertEqual(sum(decision.status == "allowed" for decision in decisions), 3)
        self.assertEqual(sum(decision.status == "limited" for decision in decisions), 21)

    def test_real_redis_refills_one_token_after_a_simulated_five_minute_interval(self):
        for _ in range(3):
            self.assertEqual(self._consume().status, "allowed")
        key = owner_rate_limit.build_owner_rate_limit_key(
            _context(), GRANT_CLASS, _configuration(), operator_user_id=USER_ID,
        )
        # Advance only this disposable fixture's existing rate debt and TTL by
        # five minutes; the production Lua and its server clock are unchanged.
        self.client.command([
            "EVAL",
            "local state = cjson.decode(redis.call('GET', KEYS[1])); "
            "local ttl = redis.call('PTTL', KEYS[1]); "
            "state.tatUs = string.format('%.0f', tonumber(state.tatUs) - 300000000); "
            "return redis.call('SET', KEYS[1], cjson.encode(state), 'PX', ttl - 300000)",
            1, key,
        ])
        self.assertEqual(self._consume().status, "allowed")
        self.assertEqual(self._consume().status, "limited")


if __name__ == "__main__":
    unittest.main()
