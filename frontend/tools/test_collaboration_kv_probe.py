from __future__ import annotations

import base64
import contextlib
import io
import json
import time
import unittest
from unittest import mock

from api.collaboration import owner_rate_limit, redis_store
from tools import collaboration_kv_probe as probe


RUN_ID = base64.urlsafe_b64encode(b"r" * 16).rstrip(b"=").decode("ascii")
TOKEN = "remote-token-value-123456789"
URL = "https://synthetic-kv.invalid"


def invoke_main(arguments: list[str], environment: dict[str, str] | None = None):
    output = io.StringIO()
    with mock.patch.object(probe, "generate_run_id", return_value=RUN_ID), contextlib.redirect_stdout(output):
        result = probe.main(arguments, environment={} if environment is None else environment)
    return result, json.loads(output.getvalue())


class ProbeSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = probe.ProbeNamespace(RUN_ID)
        self.budget = probe.ProbeBudget(0.0, remote=False)
        self.raw = mock.Mock(return_value={"result": None})
        self.transport = probe.SafeProbeTransport(self.namespace, self.budget, self.raw)
        self.policy = probe.ProbeCommandPolicy(self.namespace)

    def test_default_invocation_is_zero_network_dry_run(self):
        with mock.patch.object(
            probe,
            "build_remote_transport",
            side_effect=AssertionError("network transport must not be built"),
        ) as remote:
            exit_code, report = invoke_main([], {probe.KV_URL_ENV: URL, probe.KV_TOKEN_ENV: TOKEN})
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["networkOperations"], 0)
        self.assertEqual(report["kvWrites"], 0)
        self.assertEqual(report["credentials"], "not_read")
        remote.assert_not_called()

    def test_exact_success_response_continues_normally(self):
        self.raw.return_value = {"result": "OK"}
        key = self.namespace.key("success:value")
        self.assertEqual(self.transport.set_px(key, "synthetic", 30_000), "OK")
        self.assertEqual(self.budget.command_count, 1)
        self.assertEqual(self.budget.eval_count, 0)

    def test_safe_storage_envelopes_have_stable_redacted_diagnostics(self):
        cases = (
            (
                {"status": "unavailable", "error": {"code": "storage_unavailable"}},
                "remote_storage_unavailable",
            ),
            (
                {
                    "status": "unavailable",
                    "error": {"code": "storage_protocol_error"},
                },
                "remote_storage_protocol_error",
            ),
        )
        for payload, expected in cases:
            with self.subTest(expected=expected):
                raw = mock.Mock(return_value=payload)
                transport = probe.SafeProbeTransport(self.namespace, self.budget, raw)
                with self.assertRaises(probe.ProbeError) as caught:
                    transport.get(self.namespace.key(f"failure:{expected}"))
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(str(caught.exception), expected)

    def test_unexpected_top_level_shape_remains_response_shape_invalid(self):
        marker = "provider-error-body-must-not-surface"
        self.raw.return_value = {
            "status": "failed",
            "providerMessage": marker,
            "redisValue": "sensitive-value",
        }
        with self.assertRaises(probe.ProbeError) as caught:
            self.transport.get(self.namespace.key("unexpected:shape"))
        self.assertEqual(caught.exception.code, "response_shape_invalid")
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn("sensitive-value", str(caught.exception))

    def test_remote_storage_failures_are_inconclusive_and_skip_owner_write(self):
        for storage_code, diagnostic in (
            ("storage_unavailable", "remote_storage_unavailable"),
            ("storage_protocol_error", "remote_storage_protocol_error"),
        ):
            observed_commands: list[list[object]] = []

            def raw_factory():
                def unavailable(command: list[object]):
                    observed_commands.append(command)
                    return {
                        "status": "unavailable",
                        "error": {"code": storage_code},
                    }

                return unavailable

            with self.subTest(storage_code=storage_code):
                report = probe._run_report(
                    "remote",
                    probe.ProbeNamespace(RUN_ID),
                    raw_factory,
                )
                self.assertEqual(report["ownerReadVerdict"], "INCONCLUSIVE")
                self.assertEqual(report["ownerWriteVerdict"], "INCONCLUSIVE")
                self.assertEqual(
                    report["transportResults"],
                    [
                        {
                            "name": "transport_sanity",
                            "category": "owner_read",
                            "status": "FAIL",
                            "code": diagnostic,
                        }
                    ],
                )
                self.assertEqual(report["ttlStatus"], "not_checked_no_confirmed_write")
                self.assertEqual(report["evalCount"], 0)
                self.assertFalse(any(command[0] == "EVAL" for command in observed_commands))
                self.assertFalse(
                    any(
                        result["category"] == "owner_write"
                        for result in report["transportResults"]
                    )
                )

    def test_definitive_unexpected_provider_shape_is_incompatible(self):
        marker = "remote-body-must-stay-redacted"

        def raw_factory():
            return lambda _command: {"unexpected": marker}

        report = probe._run_report(
            "remote",
            probe.ProbeNamespace(RUN_ID),
            raw_factory,
        )
        rendered = json.dumps(report, sort_keys=True)
        self.assertEqual(report["ownerReadVerdict"], "INCOMPATIBLE")
        self.assertEqual(report["ownerWriteVerdict"], "INCONCLUSIVE")
        self.assertEqual(
            report["transportResults"][0]["code"], "response_shape_invalid"
        )
        self.assertNotIn(marker, rendered)

    def test_cleanup_failure_cannot_override_primary_transport_inconclusive(self):
        calls = 0

        def raw_factory():
            def raw(_command):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return {
                        "status": "unavailable",
                        "error": {"code": "storage_unavailable"},
                    }
                return {"unexpected": "cleanup-provider-body"}

            return raw

        report = probe._run_report(
            "remote",
            probe.ProbeNamespace(RUN_ID),
            raw_factory,
        )
        rendered = json.dumps(report, sort_keys=True)
        self.assertEqual(report["ownerReadVerdict"], "INCONCLUSIVE")
        self.assertEqual(report["ownerWriteVerdict"], "INCONCLUSIVE")
        self.assertEqual(report["ttlStatus"], "not_checked_no_confirmed_write")
        self.assertEqual(
            report["cleanupStatus"],
            "explicit_cleanup_inconclusive_ttl_fallback_active",
        )
        self.assertNotIn("cleanup-provider-body", rendered)

    def test_remote_execution_requires_both_independent_guards(self):
        with mock.patch.object(
            probe,
            "resolve_remote_configuration",
            side_effect=AssertionError("configuration must not be read"),
        ) as resolver:
            exit_code, report = invoke_main(["--execute-remote"], {})
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["failedTests"], ["remote_not_armed"])
        resolver.assert_not_called()

        exit_code, report = invoke_main(
            ["--execute-remote"],
            {probe.REMOTE_CONFIRM_ENV: "wrong"},
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["failedTests"], ["remote_not_armed"])

    def test_missing_credentials_and_unsafe_url_fail_before_network(self):
        armed = {probe.REMOTE_CONFIRM_ENV: probe.REMOTE_CONFIRM_VALUE}
        with mock.patch.object(
            probe,
            "build_remote_transport",
            side_effect=AssertionError("network transport must not be built"),
        ) as remote:
            exit_code, report = invoke_main(["--execute-remote"], armed)
            self.assertEqual(exit_code, 2)
            self.assertEqual(report["failedTests"], ["remote_configuration_invalid"])

            for unsafe_url in (
                "http://synthetic-kv.invalid",
                "https://user:password@synthetic-kv.invalid",
                "https://synthetic-kv.invalid/path",
                "https://synthetic-kv.invalid?token=value",
                " https://synthetic-kv.invalid",
            ):
                with self.subTest(url=unsafe_url):
                    values = {
                        **armed,
                        probe.KV_URL_ENV: unsafe_url,
                        probe.KV_TOKEN_ENV: TOKEN,
                    }
                    code, value = invoke_main(["--execute-remote"], values)
                    self.assertEqual(code, 2)
                    self.assertEqual(value["failedTests"], ["remote_configuration_invalid"])
        remote.assert_not_called()

    def test_arbitrary_wrong_tag_overlong_and_wildcard_keys_are_rejected(self):
        self.namespace.key("safe:key")
        invalid = (
            "cuevion:collab:v2:{cuevion-collab-v2}:thread:" + ("A" * 22),
            self.namespace.prefix.replace("{cuevion-collab-v2}", "{wrong-slot}") + "safe:key",
            self.namespace.prefix + "not-registered",
        )
        for key in invalid:
            with self.subTest(key=key), self.assertRaises(probe.ProbeError):
                self.transport.get(key)
        with self.assertRaises(probe.ProbeError):
            self.namespace.key("x" * 129)
        for suffix in ("cleanup:*", "cleanup:?", "cleanup:[x]"):
            with self.subTest(suffix=suffix), self.assertRaises(probe.ProbeError):
                self.namespace.key(suffix)
        self.raw.assert_not_called()

    def test_destructive_and_unapproved_commands_are_rejected(self):
        for command in (
            ["FLUSHDB"],
            ["FLUSHALL"],
            ["SCAN", "0"],
            ["KEYS", "*"],
            ["RANDOMKEY"],
            ["MIGRATE"],
            ["MOVE"],
            ["RENAME"],
            ["RENAMENX"],
            ["SWAPDB"],
            ["SHUTDOWN"],
            ["CONFIG"],
            ["SCRIPT", "FLUSH"],
            ["FUNCTION", "FLUSH"],
        ):
            with self.subTest(command=command), self.assertRaises(probe.ProbeError) as caught:
                self.policy.validate(command)
            self.assertEqual(caught.exception.code, "destructive_command_rejected")
        with self.assertRaises(probe.ProbeError) as caught:
            self.policy.validate(["INCR", self.namespace.key("counter")])
        self.assertEqual(caught.exception.code, "unapproved_command")

    def test_persistent_set_and_ttl_above_bound_are_rejected(self):
        key = self.namespace.key("ttl:value")
        for command, expected in (
            (["SET", key, "value"], "persistent_set_rejected"),
            (["SET", key, "value", "PX", probe.MAX_PROBE_TTL_MILLISECONDS + 1], "unsafe_ttl"),
            (["SET", key, "value", "EX", 30], "persistent_set_rejected"),
        ):
            with self.subTest(command=command), self.assertRaises(probe.ProbeError) as caught:
                self.policy.validate(command)
            self.assertEqual(caught.exception.code, expected)

    def test_only_exact_imported_scripts_are_approved(self):
        self.assertIn(owner_rate_limit._OWNER_RATE_LIMIT_LUA, probe._APPROVED_EVAL_SCRIPTS)
        self.assertIn(redis_store._CREATE_V2_THREAD_LUA, probe._APPROVED_EVAL_SCRIPTS)
        self.assertIn(
            redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA,
            probe._APPROVED_EVAL_SCRIPTS,
        )
        key = self.namespace.key("eval:value")
        with self.assertRaises(probe.ProbeError) as caught:
            self.policy.validate(["EVAL", "return 1", 1, key])
        self.assertEqual(caught.exception.code, "unapproved_eval_script")

    def test_key_and_command_budgets_are_hard_limits(self):
        self.assertEqual(probe.MAX_REMOTE_COMMANDS, 160)
        self.assertEqual(probe.MAX_REMOTE_EVAL_CALLS, 96)
        for index in range(probe.MAX_PROBE_KEYS):
            self.namespace.key(f"bounded:{index}")
        with self.assertRaises(probe.ProbeError) as caught:
            self.namespace.key("bounded:overflow")
        self.assertEqual(caught.exception.code, "probe_key_limit_exceeded")

        budget = probe.ProbeBudget(0.0, remote=False)
        budget.command_count = probe.MAX_REMOTE_COMMANDS
        with self.assertRaises(probe.ProbeError) as caught:
            budget.reserve("GET")
        self.assertEqual(caught.exception.code, "command_limit_exceeded")

    def test_credentials_and_transport_exceptions_are_redacted(self):
        environment = {
            probe.REMOTE_CONFIRM_ENV: probe.REMOTE_CONFIRM_VALUE,
            probe.KV_URL_ENV: URL,
            probe.KV_TOKEN_ENV: TOKEN,
        }

        def unsafe_builder(configuration):
            self.assertNotIn(TOKEN, repr(configuration))

            def fail(_command):
                raise RuntimeError(f"Bearer {TOKEN} {URL}?secret=exposed")

            return fail

        output = io.StringIO()
        with mock.patch.object(probe, "generate_run_id", return_value=RUN_ID), mock.patch.object(
            probe,
            "build_remote_transport",
            side_effect=unsafe_builder,
        ), contextlib.redirect_stdout(output):
            exit_code = probe.main(["--execute-remote"], environment=environment)
        rendered = output.getvalue()
        report = json.loads(rendered)
        self.assertEqual(exit_code, 2)
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(URL, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertEqual(report["transportResults"][0]["code"], "transport_unavailable")

    def test_report_schema_never_contains_synthetic_identity_values(self):
        report = probe._dry_run_report(self.namespace)
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("probe@synthetic.invalid", rendered)
        self.assertNotIn("probe-mailbox", rendered)
        self.assertNotIn(TOKEN, rendered)


class OwnerRateLimitRealRedisTests(unittest.TestCase):
    @staticmethod
    def _command(raw, command: list[object]) -> object:
        payload = raw(command)
        if type(payload) is not dict or set(payload) != {"result"}:
            raise AssertionError("unexpected local Redis response")
        return payload["result"]

    def _rate(self, raw, key: str, rate_class: str) -> dict[str, object]:
        policy = owner_rate_limit.owner_rate_limit_policy(rate_class)
        self.assertIsNotNone(policy)
        assert policy is not None
        encoded = self._command(
            raw,
            [
                "EVAL",
                owner_rate_limit._OWNER_RATE_LIMIT_LUA,
                1,
                key,
                str(policy.emission_interval_microseconds),
                str(policy.burst),
                "128",
            ],
        )
        self.assertIsInstance(encoded, str)
        result = json.loads(encoded)
        self.assertIsInstance(result, dict)
        return result

    def _shorten(self, raw, key: str, milliseconds: int) -> None:
        encoded_record = self._command(raw, ["GET", key])
        self.assertIsInstance(encoded_record, str)
        assert type(encoded_record) is str
        record = json.loads(encoded_record)
        server_time = self._command(raw, ["TIME"])
        self.assertIsInstance(server_time, list)
        assert type(server_time) is list
        now = (int(server_time[0]) * 1_000_000) + int(server_time[1])
        remaining_microseconds = max(0, int(record["tatUs"]) - now)
        state_ttl = max(1, (remaining_microseconds + 999) // 1_000)
        target = state_ttl - milliseconds
        self.assertGreater(target, 0)
        self.assertEqual(self._command(raw, ["PEXPIRE", key, target]), 1)
        after = self._command(raw, ["PTTL", key])
        self.assertIsInstance(after, int)
        assert type(after) is int
        self.assertGreater(after, 0)
        self.assertLessEqual(after, target)

    def _canonical_record(
        self,
        raw,
        *,
        offset_microseconds: int,
        v_first: bool = True,
    ) -> str:
        server_time = self._command(raw, ["TIME"])
        self.assertIsInstance(server_time, list)
        assert type(server_time) is list
        now = (int(server_time[0]) * 1_000_000) + int(server_time[1])
        tat = str(now + offset_microseconds)
        if v_first:
            return '{"v":"1","tatUs":"' + tat + '"}'
        return '{"tatUs":"' + tat + '","v":"1"}'

    def _set_aligned_ttl_delta(
        self,
        raw,
        key: str,
        ttl_delta_milliseconds: int,
        *,
        state_ttl_milliseconds: int = 5_000,
    ) -> str:
        server_time = self._command(raw, ["TIME"])
        self.assertIsInstance(server_time, list)
        assert type(server_time) is list
        now_milliseconds = (int(server_time[0]) * 1_000) + (
            int(server_time[1]) // 1_000
        )
        tat_milliseconds = now_milliseconds + state_ttl_milliseconds
        record = (
            '{"v":"1","tatUs":"'
            + str(tat_milliseconds * 1_000)
            + '"}'
        )
        self.assertEqual(
            self._command(
                raw,
                ["SET", key, record, "PX", state_ttl_milliseconds],
            ),
            "OK",
        )
        self.assertEqual(
            self._command(
                raw,
                [
                    "PEXPIREAT",
                    key,
                    tat_milliseconds + ttl_delta_milliseconds,
                ],
            ),
            1,
        )
        return record

    def test_bounded_early_expiry_skew_uses_exact_canonical_lua(self):
        tolerance = owner_rate_limit._OWNER_RATE_LIMIT_EARLY_EXPIRY_TOLERANCE_MS
        self.assertEqual(tolerance, 100)
        namespace = probe.ProbeNamespace(RUN_ID)
        with probe.LocalRedisServer() as server:
            raw = server.transport()
            for skew, expected in (
                (0, "allowed"),
                (50, "allowed"),
                (64, "allowed"),
                (tolerance, "allowed"),
                (tolerance + 1, "malformed"),
            ):
                with self.subTest(skew=skew):
                    key = namespace.key(f"rate:skew:{skew}")
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": "allowed"},
                    )
                    record = self._set_aligned_ttl_delta(raw, key, -skew)
                    self.assertEqual(self._command(raw, ["GET", key]), record)
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": expected},
                    )

    def test_bounded_late_expiry_skew_uses_exact_canonical_lua(self):
        tolerance = owner_rate_limit._OWNER_RATE_LIMIT_LATE_EXPIRY_TOLERANCE_MS
        self.assertEqual(tolerance, 25)
        namespace = probe.ProbeNamespace(RUN_ID)
        with probe.LocalRedisServer() as server:
            raw = server.transport()
            for ttl_delta, expected in (
                (0, "allowed"),
                (8, "allowed"),
                (16, "allowed"),
                (17, "allowed"),
                (tolerance, "allowed"),
                (tolerance + 1, "malformed"),
                (50, "malformed"),
            ):
                with self.subTest(ttl_delta=ttl_delta):
                    key = namespace.key(f"rate:late:{ttl_delta}")
                    record = self._set_aligned_ttl_delta(raw, key, ttl_delta)
                    self.assertEqual(self._command(raw, ["GET", key]), record)
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": expected},
                    )

    def test_state_integrity_regressions_remain_fail_closed(self):
        namespace = probe.ProbeNamespace(RUN_ID)
        with probe.LocalRedisServer() as server:
            raw = server.transport()

            missing_key = namespace.key("rate:missing")
            self.assertEqual(
                self._rate(raw, missing_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "allowed"},
            )
            self.assertEqual(
                self._rate(raw, missing_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "allowed"},
            )

            expired_key = namespace.key("rate:expired")
            self.assertEqual(
                self._rate(raw, expired_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "allowed"},
            )
            self.assertEqual(self._command(raw, ["PEXPIRE", expired_key, 1]), 1)
            deadline = time.monotonic() + 1
            while self._command(raw, ["GET", expired_key]) is not None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.002)
            self.assertEqual(
                self._rate(raw, expired_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "allowed"},
            )

            persistent_key = namespace.key("rate:persistent")
            persistent_record = self._canonical_record(
                raw,
                offset_microseconds=5_000_000,
            )
            self.assertEqual(
                self._command(raw, ["SET", persistent_key, persistent_record]),
                "OK",
            )
            self.assertEqual(
                self._rate(raw, persistent_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "malformed"},
            )

            malformed_records = (
                ("not-json", "malformed"),
                ("x" * 129, "oversized"),
                ('{"v":"1","tatUs":"01"}', "invalid-tat"),
            )
            for record, label in malformed_records:
                with self.subTest(record=label):
                    key = namespace.key(f"rate:{label}")
                    self.assertEqual(
                        self._command(raw, ["SET", key, record, "PX", 1_000]),
                        "OK",
                    )
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": "malformed"},
                    )
                    self.assertEqual(self._command(raw, ["GET", key]), record)

            future_key = namespace.key("rate:future-debt")
            future_record = self._canonical_record(
                raw,
                offset_microseconds=15_100_000,
            )
            self.assertEqual(
                self._command(raw, ["SET", future_key, future_record, "PX", 15_000]),
                "OK",
            )
            self.assertEqual(
                self._rate(raw, future_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "malformed"},
            )

            for v_first in (True, False):
                with self.subTest(v_first=v_first):
                    key = namespace.key(f"rate:canonical:{int(v_first)}")
                    record = self._canonical_record(
                        raw,
                        offset_microseconds=5_000_000,
                        v_first=v_first,
                    )
                    self.assertEqual(
                        self._command(raw, ["SET", key, record, "PX", 4_950]),
                        "OK",
                    )
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": "allowed"},
                    )

            noncanonical_key = namespace.key("rate:noncanonical")
            noncanonical_record = self._canonical_record(
                raw,
                offset_microseconds=5_000_000,
            ).replace(":", ": ", 1)
            self.assertEqual(
                self._command(
                    raw,
                    ["SET", noncanonical_key, noncanonical_record, "PX", 4_950],
                ),
                "OK",
            )
            self.assertEqual(
                self._rate(raw, noncanonical_key, owner_rate_limit.RATE_LIMIT_READ),
                {"status": "malformed"},
            )

    def test_rate_policies_and_tolerated_skew_keep_enforcement_effective(self):
        namespace = probe.ProbeNamespace(RUN_ID)
        with probe.LocalRedisServer() as server:
            raw = server.transport()
            expected_retry = {
                owner_rate_limit.RATE_LIMIT_BOOTSTRAP: "5",
                owner_rate_limit.RATE_LIMIT_READ: "1",
                owner_rate_limit.RATE_LIMIT_WRITE: "2",
            }
            for rate_class in (
                owner_rate_limit.RATE_LIMIT_BOOTSTRAP,
                owner_rate_limit.RATE_LIMIT_READ,
                owner_rate_limit.RATE_LIMIT_WRITE,
            ):
                with self.subTest(rate_class=rate_class):
                    policy = owner_rate_limit.owner_rate_limit_policy(rate_class)
                    self.assertIsNotNone(policy)
                    assert policy is not None
                    key = namespace.key(f"rate:policy:{rate_class}")
                    for _ in range(policy.burst):
                        self.assertEqual(
                            self._rate(raw, key, rate_class),
                            {"status": "allowed"},
                        )
                    self.assertEqual(
                        self._rate(raw, key, rate_class),
                        {
                            "status": "limited",
                            "retryAfter": expected_retry[rate_class],
                        },
                    )
                    if rate_class == owner_rate_limit.RATE_LIMIT_READ:
                        self._shorten(raw, key, 64)
                        self.assertEqual(
                            self._rate(raw, key, rate_class),
                            {"status": "limited", "retryAfter": "1"},
                        )
                        time.sleep(0.60)
                        self.assertEqual(
                            self._rate(raw, key, rate_class),
                            {"status": "allowed"},
                        )

            positive_skew_key = namespace.key("rate:policy:read-positive")
            self._set_aligned_ttl_delta(
                raw,
                positive_skew_key,
                25,
                state_ttl_milliseconds=15_000,
            )
            self.assertEqual(
                self._rate(
                    raw,
                    positive_skew_key,
                    owner_rate_limit.RATE_LIMIT_READ,
                ),
                {"status": "limited", "retryAfter": "1"},
            )

    def test_redis_command_failures_and_time_shapes_remain_closed(self):
        namespace = probe.ProbeNamespace(RUN_ID)
        for command_name, expected, needs_state in (
            ("SET", "unavailable", False),
            ("GET", "unavailable", False),
            ("PTTL", "malformed", True),
        ):
            with self.subTest(command=command_name), probe.LocalRedisServer() as server:
                raw = server.transport()
                key = namespace.key(f"rate:failure:{command_name.lower()}")
                if needs_state:
                    self.assertEqual(
                        self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                        {"status": "allowed"},
                    )
                self.assertEqual(
                    self._command(
                        raw,
                        ["ACL", "SETUSER", "default", f"-{command_name.lower()}"],
                    ),
                    "OK",
                )
                self.assertEqual(
                    self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                    {"status": expected},
                )

        for label, command_renames in (
            ("unavailable", (("TIME", ""),)),
            ("malformed", (("TIME", "ORIGINALTIME"), ("PING", "TIME"))),
        ):
            with self.subTest(time_shape=label), probe.LocalRedisServer(
                command_renames=command_renames,
            ) as server:
                raw = server.transport()
                key = namespace.key(f"rate:time:{label}")
                self.assertEqual(
                    self._rate(raw, key, owner_rate_limit.RATE_LIMIT_READ),
                    {"status": "unavailable"},
                )


class ProbeLocalCompatibilityTests(unittest.TestCase):
    def test_exact_production_scripts_pass_against_fresh_real_redis(self):
        namespace = probe.ProbeNamespace(RUN_ID)
        observed_commands: list[list[object]] = []
        with probe.LocalRedisServer() as server:
            def raw_factory():
                raw = server.transport()

                def record(command: list[object]):
                    observed_commands.append(command)
                    return raw(command)

                return record

            report = probe._run_report("local", namespace, raw_factory)

        self.assertEqual(report["ownerReadVerdict"], "OWNER_READ_KV_COMPATIBLE")
        self.assertEqual(report["ownerWriteVerdict"], "OWNER_WRITE_KV_COMPATIBLE")
        self.assertEqual(report["failedTests"], [])
        self.assertLessEqual(report["keyCount"], probe.MAX_PROBE_KEYS)
        self.assertLessEqual(report["commandCount"], probe.MAX_REMOTE_COMMANDS)
        self.assertLessEqual(report["evalCount"], probe.MAX_REMOTE_EVAL_CALLS)
        self.assertEqual(
            {command[0] for command in observed_commands},
            {"SET", "GET", "PTTL", "TIME", "EVAL", "DEL"},
        )
        eval_scripts = {command[1] for command in observed_commands if command[0] == "EVAL"}
        self.assertEqual(
            eval_scripts,
            {
                probe._CJSON_PROBE_LUA,
                owner_rate_limit._OWNER_RATE_LIMIT_LUA,
                redis_store._CREATE_V2_THREAD_LUA,
                redis_store._APPEND_V2_OWNER_IDEMPOTENT_LUA,
            },
        )
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("probe@synthetic.invalid", rendered)
        self.assertNotIn("probe-mailbox", rendered)


if __name__ == "__main__":
    unittest.main()
