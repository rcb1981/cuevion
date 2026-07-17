"""Offline tests for the secret-safe database configuration boundary."""

import ast
import copy
import dataclasses
from pathlib import Path
import pickle
from types import MappingProxyType
import unittest

from cuevion_db import configuration as config


_FRONTEND = Path(__file__).resolve().parents[2]
_SOURCE = _FRONTEND / "cuevion_db" / "configuration.py"
_SECRET_PARTS = (
    "runtime-user",
    "runtime-password",
    "migration-user",
    "migration-password",
    "ep-foundation-pooler.eu.neon.tech",
    "ep-foundation.eu.neon.tech",
    "authority_database",
)


def _environment(target: str = "production") -> dict[str, str]:
    return {
        "CUEVION_DATABASE_URL": (
            "postgresql://runtime-user:runtime-password@"
            "ep-foundation-pooler.eu.neon.tech:5432/authority_database"
            "?sslmode=require&channel_binding=require"
        ),
        "CUEVION_DATABASE_URL_UNPOOLED": (
            "postgresql+psycopg://migration-user:migration-password@"
            "ep-foundation.eu.neon.tech:5432/authority_database"
            "?sslmode=require"
        ),
        "CUEVION_DATABASE_TARGET": target,
        "VERCEL_ENV": target,
        "PSYCOPG_IMPL": "binary",
    }


class ConfigurationSurfaceTests(unittest.TestCase):
    def test_exact_public_surface_and_closed_target(self):
        self.assertEqual(
            config.__all__,
            (
                "DatabaseConfigurationError",
                "DatabaseTarget",
                "RuntimeDatabaseUrl",
                "MigrationDatabaseUrl",
                "DatabaseConfiguration",
                "parse_database_configuration",
            ),
        )
        self.assertEqual(
            tuple((member.name, member.value) for member in config.DatabaseTarget),
            (("PRODUCTION", "production"), ("PREVIEW", "preview")),
        )
        with self.assertRaises(config.DatabaseConfigurationError):
            config.DatabaseTarget("development")

    def test_records_are_slotted_immutable_parser_controlled_and_not_serializable(self):
        value = config.parse_database_configuration(
            MappingProxyType(_environment())
        )
        records = (value, value.runtime_url, value.migration_url)
        for record in records:
            with self.subTest(record=type(record).__name__):
                self.assertFalse(dataclasses.is_dataclass(record))
                self.assertFalse(hasattr(record, "__dict__"))
                self.assertIs(copy.copy(record), record)
                self.assertIs(copy.deepcopy(record), record)
                with self.assertRaises(config.DatabaseConfigurationError):
                    setattr(record, "extra", "secret")
                for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
                    with self.assertRaises(config.DatabaseConfigurationError):
                        pickle.dumps(record, protocol=protocol)
        with self.assertRaises(config.DatabaseConfigurationError):
            config.RuntimeDatabaseUrl(object(), "secret", "secret", "secret")
        with self.assertRaises(config.DatabaseConfigurationError):
            class ForbiddenSubclass(config.DatabaseConfiguration):
                pass

    def test_repr_and_str_are_fully_redacted(self):
        value = config.parse_database_configuration(_environment())
        for record in (value, value.runtime_url, value.migration_url):
            rendered = repr(record) + str(record)
            for part in _SECRET_PARTS:
                self.assertNotIn(part, rendered)


class ConfigurationParsingTests(unittest.TestCase):
    def assert_invalid(self, values: object) -> None:
        with self.assertRaises(config.DatabaseConfigurationError) as caught:
            config.parse_database_configuration(values)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.args, ())
        self.assertEqual(str(caught.exception), "invalid database configuration")
        self.assertEqual(repr(caught.exception), "DatabaseConfigurationError()")
        rendered = str(caught.exception) + repr(caught.exception)
        for part in _SECRET_PARTS:
            self.assertNotIn(part, rendered)

    def test_valid_production_and_preview_are_normalized_without_fallback(self):
        production = config.parse_database_configuration(_environment())
        preview = config.parse_database_configuration(_environment("preview"))
        self.assertIs(production.target, config.DatabaseTarget.PRODUCTION)
        self.assertIs(preview.target, config.DatabaseTarget.PREVIEW)
        self.assertTrue(production.runtime_url.value.startswith("postgresql+psycopg://"))
        self.assertTrue(production.migration_url.value.startswith("postgresql+psycopg://"))
        self.assertIn("channel_binding=require", production.runtime_url.value)
        self.assertNotIn("channel_binding", production.migration_url.value)

    def test_missing_empty_whitespace_control_and_nonmapping_values_fail_closed(self):
        for name in tuple(_environment()):
            missing = _environment()
            del missing[name]
            self.assert_invalid(missing)
            for rejected in ("", " value", "value ", "value\n"):
                invalid = _environment()
                invalid[name] = rejected
                self.assert_invalid(invalid)
        self.assert_invalid(None)
        self.assert_invalid([])

        for encoded_control in ("runtime\u0085password", "runtime%C2%85password"):
            invalid = _environment()
            invalid["CUEVION_DATABASE_URL"] = invalid[
                "CUEVION_DATABASE_URL"
            ].replace("runtime-password", encoded_control)
            self.assert_invalid(invalid)

    def test_malformed_userinfo_and_user_password_separators_fail_closed(self):
        for replacement in (
            "runtime-user:runtime-password@@",
            ":runtime-password@",
            "runtime-user:@",
            "runtime-user@",
            "runtime-user::runtime-password@",
        ):
            with self.subTest(userinfo=replacement):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace("runtime-user:runtime-password@", replacement)
                self.assert_invalid(values)

    def test_percent_decoded_controls_in_url_components_fail_closed(self):
        mutations = (
            (
                "runtime-user",
                "runtime%C2%85user",
            ),
            (
                "runtime-password",
                "runtime%C2%85password",
            ),
            (
                "ep-foundation-pooler.eu.neon.tech",
                "ep-foundation%C2%85-pooler.eu.neon.tech",
            ),
            (
                "/authority_database?",
                "/authority%C2%85database?",
            ),
            (
                "?sslmode=require",
                "?ssl%C2%85mode=require",
            ),
            (
                "?sslmode=require",
                "?sslmode=req%C2%85uire",
            ),
        )
        for old, new in mutations:
            with self.subTest(component=new):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace(old, new)
                self.assert_invalid(values)

    def test_invalid_percent_encoding_in_url_components_fails_closed(self):
        mutations = (
            ("runtime-user", "runtime%user"),
            ("runtime-password", "runtime%GGpassword"),
            (
                "ep-foundation-pooler.eu.neon.tech",
                "ep-foundation%ZZ-pooler.eu.neon.tech",
            ),
            ("/authority_database?", "/authority%2_database?"),
            ("?sslmode=require", "?sslmode=%require"),
        )
        for old, new in mutations:
            with self.subTest(component=new):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace(old, new)
                self.assert_invalid(values)

    def test_ipv6_unicode_and_percent_encoded_hosts_fail_closed(self):
        for endpoint in (
            "[2001:db8::1]",
            "ep-f\u00f6undation-pooler.eu.neon.tech",
            "ep-f%C3%B6undation-pooler.eu.neon.tech",
        ):
            with self.subTest(endpoint=endpoint):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace("ep-foundation-pooler.eu.neon.tech", endpoint)
                self.assert_invalid(values)

    def test_explicit_and_implicit_default_ports_are_equivalent(self):
        explicit = config.parse_database_configuration(_environment())
        self.assertIn(":5432/authority_database", explicit.runtime_url.value)
        self.assertIn(":5432/authority_database", explicit.migration_url.value)

        implicit_values = _environment()
        for key in (
            "CUEVION_DATABASE_URL",
            "CUEVION_DATABASE_URL_UNPOOLED",
        ):
            implicit_values[key] = implicit_values[key].replace(
                ":5432/authority_database", "/authority_database"
            )
        implicit = config.parse_database_configuration(implicit_values)
        self.assertNotIn(":5432/authority_database", implicit.runtime_url.value)
        self.assertNotIn(":5432/authority_database", implicit.migration_url.value)

        mixed_values = _environment()
        mixed_values["CUEVION_DATABASE_URL_UNPOOLED"] = mixed_values[
            "CUEVION_DATABASE_URL_UNPOOLED"
        ].replace(":5432/authority_database", "/authority_database")
        mixed = config.parse_database_configuration(mixed_values)
        self.assertIn(":5432/authority_database", mixed.runtime_url.value)
        self.assertNotIn(":5432/authority_database", mixed.migration_url.value)

    def test_invalid_explicit_ports_fail_closed(self):
        for port in ("0", "65536", "not-a-port"):
            with self.subTest(port=port):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace(":5432/authority_database", f":{port}/authority_database")
                self.assert_invalid(values)

    def test_duplicate_empty_query_keys_and_values_fail_closed(self):
        for query in (
            "sslmode=require&sslmode=require",
            "sslmode=require&channel_binding=require&channel_binding=require",
            "=require",
            "sslmode=",
            "sslmode=require&channel_binding=",
        ):
            with self.subTest(query=query):
                values = _environment()
                values["CUEVION_DATABASE_URL"] = values[
                    "CUEVION_DATABASE_URL"
                ].replace(
                    "sslmode=require&channel_binding=require",
                    query,
                )
                self.assert_invalid(values)

    def test_scheme_fragment_query_and_tls_contract(self):
        runtime_key = "CUEVION_DATABASE_URL"
        replacements = (
            ("postgresql://", "postgresql+psycopg2://"),
            ("?sslmode=require", "?sslmode=disable"),
            ("?sslmode=require", "?sslmode=require&unknown=value"),
            ("?sslmode=require", "?sslmode=require&sslmode=require"),
            ("?sslmode=require", "?sslmode=require&channel_binding=disable"),
            ("?sslmode=require", "?sslmode=require#fragment"),
        )
        for old, new in replacements:
            values = _environment()
            values[runtime_key] = values[runtime_key].replace(old, new)
            self.assert_invalid(values)

    def test_pooled_direct_and_logical_database_boundaries(self):
        mutations = (
            (
                "CUEVION_DATABASE_URL",
                "ep-foundation-pooler.eu.neon.tech",
                "ep-foundation.eu.neon.tech",
            ),
            (
                "CUEVION_DATABASE_URL_UNPOOLED",
                "ep-foundation.eu.neon.tech",
                "ep-foundation-pooler.eu.neon.tech",
            ),
            (
                "CUEVION_DATABASE_URL",
                "ep-foundation-pooler.eu.neon.tech",
                "example-pooler.invalid",
            ),
            (
                "CUEVION_DATABASE_URL_UNPOOLED",
                "authority_database",
                "other_database",
            ),
            (
                "CUEVION_DATABASE_URL_UNPOOLED",
                ":5432/authority_database",
                ":5433/authority_database",
            ),
        )
        for key, old, new in mutations:
            values = _environment()
            values[key] = values[key].replace(old, new)
            self.assert_invalid(values)

    def test_neon_hosts_obey_dns_label_and_length_grammar(self):
        long_label = "a" * 64
        long_host_suffix = ".".join(("a" * 63,) * 4) + ".neon.tech"
        invalid_endpoint_pairs = (
            (
                "ep-bad_-pooler.eu.neon.tech",
                "ep-bad_.eu.neon.tech",
            ),
            (
                "ep-bad-pooler..neon.tech",
                "ep-bad..neon.tech",
            ),
            (
                "ep-bad-pooler.-eu.neon.tech",
                "ep-bad.-eu.neon.tech",
            ),
            (
                "ep-bad-pooler.eu-.neon.tech",
                "ep-bad.eu-.neon.tech",
            ),
            (
                f"ep-bad-pooler.{long_label}.neon.tech",
                f"ep-bad.{long_label}.neon.tech",
            ),
            (
                f"ep-bad-pooler.{long_host_suffix}",
                f"ep-bad.{long_host_suffix}",
            ),
        )
        for runtime_endpoint, migration_endpoint in invalid_endpoint_pairs:
            values = _environment()
            values["CUEVION_DATABASE_URL"] = values[
                "CUEVION_DATABASE_URL"
            ].replace("ep-foundation-pooler.eu.neon.tech", runtime_endpoint)
            values["CUEVION_DATABASE_URL_UNPOOLED"] = values[
                "CUEVION_DATABASE_URL_UNPOOLED"
            ].replace("ep-foundation.eu.neon.tech", migration_endpoint)
            self.assert_invalid(values)

    def test_target_vercel_environment_and_psycopg_impl_are_closed(self):
        for key, value in (
            ("CUEVION_DATABASE_TARGET", "development"),
            ("CUEVION_DATABASE_TARGET", "staging"),
            ("VERCEL_ENV", "preview"),
            ("VERCEL_ENV", "development"),
            ("PSYCOPG_IMPL", "python"),
            ("PSYCOPG_IMPL", "Binary"),
        ):
            values = _environment()
            values[key] = value
            self.assert_invalid(values)

    def test_unknown_environment_keys_are_ignored_without_value_disclosure(self):
        values = _environment()
        values["UNRELATED_SECRET"] = "never-render-this-unrelated-value"
        parsed = config.parse_database_configuration(values)
        self.assertIs(parsed.target, config.DatabaseTarget.PRODUCTION)

    def test_errors_retain_no_url_components_in_module_traceback_locals(self):
        values = _environment()
        values["CUEVION_DATABASE_URL"] = values["CUEVION_DATABASE_URL"].replace(
            "sslmode=require", "sslmode=disable"
        )
        try:
            config.parse_database_configuration(values)
        except config.DatabaseConfigurationError as error:
            traceback = error.__traceback__
            while traceback is not None:
                if traceback.tb_frame.f_globals.get("__name__") == config.__name__:
                    rendered = repr(traceback.tb_frame.f_locals)
                    for part in _SECRET_PARTS:
                        self.assertNotIn(part, rendered)
                traceback = traceback.tb_next
        else:
            self.fail("invalid configuration unexpectedly succeeded")

    def test_error_chaining_and_traceback_retain_no_synthetic_secrets(self):
        synthetic_secrets = (
            "trace-runtime-user",
            "trace-runtime-password",
            "trace-migration-user",
            "trace-migration-password",
        )
        values = _environment()
        values["CUEVION_DATABASE_URL"] = values[
            "CUEVION_DATABASE_URL"
        ].replace("runtime-user", synthetic_secrets[0]).replace(
            "runtime-password", synthetic_secrets[1]
        ).replace("sslmode=require", "sslmode=disable")
        values["CUEVION_DATABASE_URL_UNPOOLED"] = values[
            "CUEVION_DATABASE_URL_UNPOOLED"
        ].replace("migration-user", synthetic_secrets[2]).replace(
            "migration-password", synthetic_secrets[3]
        )

        try:
            config.parse_database_configuration(values)
        except config.DatabaseConfigurationError as error:
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            traceback = error.__traceback__
            configuration_frames = 0
            while traceback is not None:
                if traceback.tb_frame.f_globals.get("__name__") == config.__name__:
                    configuration_frames += 1
                    rendered = repr(traceback.tb_frame.f_locals)
                    for secret in synthetic_secrets:
                        self.assertNotIn(secret, rendered)
                traceback = traceback.tb_next
            self.assertGreater(configuration_frames, 0)
        else:
            self.fail("invalid configuration unexpectedly succeeded")


class ConfigurationInactivityTests(unittest.TestCase):
    def test_source_has_no_environment_io_engine_or_connection(self):
        source = _SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("os", imported_roots)
        self.assertNotIn("socket", imported_roots)
        self.assertNotIn("psycopg", imported_roots)
        normalized = source.casefold()
        for forbidden in ("create_engine", "os.environ", "socket.", "connect(", "logging"):
            self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()
