from __future__ import annotations

import base64
import copy
import hashlib
import json
import pickle
import unittest

from . import owner_rate_limit
from .owner_request_security import (
    VerifiedOwnerAuthentication,
    resolve_owner_request_context,
)


NOW = 1_800_000_000
OWNER_EMAIL = "owner@example.com"
WORKSPACE_ID = "wsp_" + ("w" * 22)
RATE_KEY = b"dedicated-owner-rate-limit-key-0001"
OTHER_KEY = b"different-owner-security-key-000001"
OTHER_MAILBOX_KEY = b"m" * 32


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _configuration(**updates: str):
    values = {owner_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY)}
    values.update(updates)
    return owner_rate_limit.parse_owner_rate_limit_configuration(values)


def _context(**updates: object):
    values: dict[str, object] = {
        "issuer": "https://cuevion.eu.auth0.com/",
        "authentication_version": 1,
        "subject": "auth0|owner-subject",
        "owner_email": OWNER_EMAIL,
        "workspace_id": WORKSPACE_ID,
        "display_name": "Owner Person",
        "session_id": _b64(b"s" * 32),
        "credential_digest": _b64(hashlib.sha256(b"binding").digest()),
        "issued_at": NOW - 60,
        "expires_at": NOW + 3_600,
    }
    values.update(updates)
    claims = VerifiedOwnerAuthentication(**values)  # type: ignore[arg-type]
    return resolve_owner_request_context(
        (),
        authentication_resolver=lambda _headers: claims,
        now=NOW,
    )


class OwnerRateLimitUnitTests(unittest.TestCase):
    def test_private_beta_policies_are_exact(self):
        expected = {
            owner_rate_limit.RATE_LIMIT_BOOTSTRAP: (5_000_000, 4, 12),
            owner_rate_limit.RATE_LIMIT_READ: (500_000, 30, 120),
            owner_rate_limit.RATE_LIMIT_WRITE: (2_000_000, 10, 30),
        }
        for rate_class, (interval, burst, per_minute) in expected.items():
            policy = owner_rate_limit.owner_rate_limit_policy(rate_class)
            self.assertIsNotNone(policy)
            assert policy is not None
            self.assertEqual(policy.emission_interval_microseconds, interval)
            self.assertEqual(policy.burst, burst)
            self.assertEqual(60_000_000 // interval, per_minute)

    def test_storage_expiry_invariant_is_explicit_and_policy_bounded(self):
        self.assertEqual(owner_rate_limit.STATE_EXPIRY_GRACE_MS, 1_000)
        self.assertEqual(
            owner_rate_limit._OWNER_RATE_LIMIT_EARLY_EXPIRY_TOLERANCE_MS,
            100,
        )
        self.assertEqual(
            owner_rate_limit._OWNER_RATE_LIMIT_TTL_OBSERVATION_ALLOWANCE_MS,
            100,
        )
        script = owner_rate_limit._OWNER_RATE_LIMIT_LUA
        self.assertIn("local EARLY_EXPIRY_TOLERANCE_MS = 100", script)
        self.assertIn("local STATE_EXPIRY_GRACE_MS = 1000", script)
        self.assertIn("local TTL_OBSERVATION_ALLOWANCE_MS = 100", script)
        self.assertIn(
            "stateTtl - EARLY_EXPIRY_TOLERANCE_MS",
            script,
        )
        self.assertIn(
            "+ STATE_EXPIRY_GRACE_MS\n    + TTL_OBSERVATION_ALLOWANCE_MS",
            script,
        )
        self.assertIn(
            "+ STATE_EXPIRY_GRACE_MS\nlocal candidate",
            script,
        )
        self.assertNotIn("currentTtl > stateTtl", script)
        self.assertNotIn("LATE_EXPIRY_TOLERANCE", script)

        expected_maximum_ttls = {
            owner_rate_limit.RATE_LIMIT_BOOTSTRAP: 21_100,
            owner_rate_limit.RATE_LIMIT_READ: 16_100,
            owner_rate_limit.RATE_LIMIT_WRITE: 21_100,
        }
        for rate_class, expected_maximum in expected_maximum_ttls.items():
            policy = owner_rate_limit.owner_rate_limit_policy(rate_class)
            self.assertIsNotNone(policy)
            assert policy is not None
            maximum = (
                (policy.emission_interval_microseconds * policy.burst + 999)
                // 1_000
                + owner_rate_limit.STATE_EXPIRY_GRACE_MS
                + owner_rate_limit._OWNER_RATE_LIMIT_TTL_OBSERVATION_ALLOWANCE_MS
            )
            self.assertEqual(maximum, expected_maximum)

    def test_configuration_is_default_closed_canonical_and_opaque(self):
        for value in ({}, {owner_rate_limit.RATE_LIMIT_HMAC_ENV: "short"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner_rate_limit.parse_owner_rate_limit_configuration(value)
        with self.assertRaises(ValueError):
            owner_rate_limit.parse_owner_rate_limit_configuration(
                {owner_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(RATE_KEY) + "="}
            )

        configuration = _configuration()
        self.assertEqual(repr(configuration), "<OwnerRateLimitConfiguration>")
        self.assertNotIn(_b64(RATE_KEY), repr(configuration))
        for callback in (
            lambda: copy.copy(configuration),
            lambda: copy.deepcopy(configuration),
            lambda: pickle.dumps(configuration),
        ):
            with self.assertRaises(TypeError):
                callback()

    def test_rate_key_must_be_distinct_from_every_known_security_key(self):
        for name in (
            "CUEVION_COLLAB_V2_OWNER_CSRF_KEY",
            "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS",
            "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY",
            "CUEVION_COLLAB_INDEX_HMAC_KEY",
            "CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                _configuration(**{name: _b64(RATE_KEY)})
            parsed = _configuration(**{name: _b64(OTHER_KEY)})
            self.assertIsInstance(
                parsed,
                owner_rate_limit.OwnerRateLimitConfiguration,
            )

        padded_rate_key = base64.urlsafe_b64encode(RATE_KEY).decode("ascii")
        with self.assertRaises(ValueError):
            _configuration(MAILBOX_SECRET_ENCRYPTION_KEY=padded_rate_key)
        parsed = _configuration(
            MAILBOX_SECRET_ENCRYPTION_KEY=base64.urlsafe_b64encode(
                OTHER_MAILBOX_KEY
            ).decode("ascii")
        )
        self.assertIsInstance(parsed, owner_rate_limit.OwnerRateLimitConfiguration)
        with self.assertRaises(ValueError):
            _configuration(MAILBOX_SECRET_ENCRYPTION_KEY=_b64(b"m" * 33))

        with self.assertRaises(ValueError):
            _configuration(CUEVION_AUTH_SESSION_SECRET=_b64(RATE_KEY))
        with self.assertRaises(ValueError):
            _configuration(CUEVION_AUTH_SESSION_SECRET=" " + ("s" * 32))

    def test_key_uses_only_verified_canonical_owner_workspace_hmac(self):
        configuration = _configuration()
        context = _context()
        key = owner_rate_limit.build_owner_rate_limit_key(
            context,
            owner_rate_limit.RATE_LIMIT_READ,
            configuration,
        )
        self.assertIsNotNone(key)
        assert key is not None
        self.assertIn("{cuevion-collab-v2}", key)
        self.assertNotIn(OWNER_EMAIL, key)
        self.assertNotIn(WORKSPACE_ID, key)
        self.assertNotIn(context.subject, key)
        self.assertNotIn(_b64(RATE_KEY), key)

        same_owner_new_session = _context(
            subject="auth0|rotated-subject",
            session_id=_b64(b"t" * 32),
        )
        self.assertEqual(
            owner_rate_limit.build_owner_rate_limit_key(
                same_owner_new_session,
                owner_rate_limit.RATE_LIMIT_READ,
                configuration,
            ),
            key,
        )
        self.assertNotEqual(
            owner_rate_limit.build_owner_rate_limit_key(
                _context(owner_email="other@example.com"),
                owner_rate_limit.RATE_LIMIT_READ,
                configuration,
            ),
            key,
        )
        self.assertNotEqual(
            owner_rate_limit.build_owner_rate_limit_key(
                _context(workspace_id="wsp_" + ("x" * 22)),
                owner_rate_limit.RATE_LIMIT_READ,
                configuration,
            ),
            key,
        )
        self.assertIsNone(
            owner_rate_limit.build_owner_rate_limit_key(
                {"owner_email": OWNER_EMAIL, "workspace_id": WORKSPACE_ID},
                owner_rate_limit.RATE_LIMIT_READ,
                configuration,
            )
        )

    def test_one_atomic_eval_has_no_pii_secret_scan_or_keys_command(self):
        commands: list[list] = []

        def transport(command: list) -> dict:
            commands.append(command)
            return {"result": json.dumps({"status": "allowed"})}

        result = owner_rate_limit.consume_owner_rate_limit(
            _context(),
            owner_rate_limit.RATE_LIMIT_WRITE,
            _configuration(),
            command_transport=transport,
        )
        self.assertEqual(result, owner_rate_limit.OwnerRateLimitDecision("allowed"))
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command[0], "EVAL")
        self.assertEqual(command[2], 1)
        self.assertEqual(len(command), 7)
        serialized = json.dumps(command)
        self.assertNotIn(OWNER_EMAIL, serialized)
        self.assertNotIn(WORKSPACE_ID, serialized)
        self.assertNotIn(_b64(RATE_KEY), serialized)
        script = command[1]
        self.assertIn("redis.call, 'TIME'", script)
        self.assertNotIn("redis.call, 'SCAN'", script)
        self.assertNotIn("redis.call, 'KEYS'", script)

    def test_limited_and_storage_failures_are_closed(self):
        limited = owner_rate_limit.consume_owner_rate_limit(
            _context(),
            owner_rate_limit.RATE_LIMIT_READ,
            _configuration(),
            command_transport=lambda _command: {
                "result": json.dumps({"status": "limited", "retryAfter": "1"})
            },
        )
        self.assertEqual(
            limited,
            owner_rate_limit.OwnerRateLimitDecision("limited", 1),
        )
        for transport in (
            lambda _command: {"result": json.dumps({"status": "malformed"})},
            lambda _command: {"result": "not-json"},
            lambda _command: (_ for _ in ()).throw(OSError("offline")),
        ):
            with self.subTest(transport=transport):
                self.assertEqual(
                    owner_rate_limit.consume_owner_rate_limit(
                        _context(),
                        owner_rate_limit.RATE_LIMIT_READ,
                        _configuration(),
                        command_transport=transport,
                    ),
                    owner_rate_limit.OwnerRateLimitDecision("unavailable"),
                )

    def test_key_rotation_starts_a_new_short_lived_namespace(self):
        context = _context()
        first = owner_rate_limit.build_owner_rate_limit_key(
            context,
            owner_rate_limit.RATE_LIMIT_WRITE,
            _configuration(),
        )
        second = owner_rate_limit.build_owner_rate_limit_key(
            context,
            owner_rate_limit.RATE_LIMIT_WRITE,
            owner_rate_limit.parse_owner_rate_limit_configuration(
                {owner_rate_limit.RATE_LIMIT_HMAC_ENV: _b64(OTHER_KEY)}
            ),
        )
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
