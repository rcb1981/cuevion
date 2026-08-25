from __future__ import annotations

import base64
import contextlib
import io
import json
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
