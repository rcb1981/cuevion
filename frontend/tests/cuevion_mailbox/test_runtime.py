"""Tests for inactive mailbox PostgreSQL shadow runtime composition."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from cuevion_mailbox import runtime


_FRONTEND = Path(__file__).resolve().parents[2]
_RUNTIME = _FRONTEND / "cuevion_mailbox" / "runtime.py"

_PROD_HOST = "ep-example-pooler.c-4.eu-central-1.aws.neon.tech"
_READER_ROLE = "cuevion_production_mailbox_reader_v1"
_WRITER_ROLE = "cuevion_production_mailbox_writer_v1"


def _url(role: str, password: str, *, host: str = _PROD_HOST) -> str:
    return (
        f"postgresql://{role}:{password}@{host}/neondb"
        "?channel_binding=require&sslmode=require"
    )


class _PgConn:
    def __init__(self, ssl_in_use: bool = True) -> None:
        self.ssl_in_use = ssl_in_use


class _Info:
    def __init__(self, user: str, dbname: str) -> None:
        self.user = user
        self.dbname = dbname


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.closed = False

    def execute(self, sql: str) -> None:
        if self.closed:
            raise AssertionError("closed cursor")
        self.connection.sql.append(sql)

    def close(self) -> None:
        if self.closed:
            raise AssertionError("cursor closed twice")
        self.closed = True


class _Connection:
    def __init__(
        self,
        *,
        user: str,
        dbname: str = "neondb",
        ssl_in_use: bool = True,
        autocommit: bool = False,
    ) -> None:
        self.autocommit = autocommit
        self.pgconn = _PgConn(ssl_in_use)
        self.info = _Info(user, dbname)
        self.closed = False
        self.sql: list[str] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.closed = True


class _Connector:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.calls: list[tuple[str, bool, int]] = []

    def __call__(
        self,
        conninfo: str,
        *,
        autocommit: bool,
        connect_timeout: int,
    ) -> _Connection:
        self.calls.append((conninfo, autocommit, connect_timeout))
        return self.connection


class MailboxRuntimeConfigurationTests(unittest.TestCase):
    def test_missing_mode_is_disabled_and_requires_no_secrets(self):
        config = runtime.parse_mailbox_runtime_configuration({})
        self.assertIs(config.mode, runtime.MailboxRuntimeMode.DISABLED)
        self.assertIsNone(config.reader_database_url)
        self.assertIsNone(config.writer_database_url)

    def test_active_read_is_preview_reader_only_and_production_rejected(self):
        preview_reader = _url(
            "cuevion_preview_mailbox_reader_v1",
            "reader-secret",
        )
        config = runtime.parse_mailbox_runtime_configuration(
            {
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
                "VERCEL_ENV": "preview",
                "CUEVION_MAILBOX_READER_DATABASE_URL": preview_reader,
            }
        )
        self.assertIs(config.mode, runtime.MailboxRuntimeMode.ACTIVE_READ)
        self.assertEqual(
            config.reader_database_url.role,
            "cuevion_preview_mailbox_reader_v1",
        )
        self.assertIsNone(config.writer_database_url)

        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(
                {
                    "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
                    "VERCEL_ENV": "production",
                    "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                        _READER_ROLE,
                        "reader-secret",
                    ),
                }
            )

    def test_production_read_mode_is_reader_only_and_preview_rejected(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_read",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                _READER_ROLE,
                "reader-secret",
            ),
        }
        config = runtime.parse_mailbox_runtime_configuration(environment)
        self.assertIs(config.mode, runtime.MailboxRuntimeMode.PRODUCTION_READ)
        self.assertEqual(config.reader_database_url.role, _READER_ROLE)
        self.assertIsNone(config.writer_database_url)

        preview = dict(environment)
        preview["VERCEL_ENV"] = "preview"
        preview["CUEVION_MAILBOX_READER_DATABASE_URL"] = _url(
            "cuevion_preview_mailbox_reader_v1",
            "reader-secret",
        )
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(preview)

    def test_production_read_authority_requires_both_exact_switches(self):
        base = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_read",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                _READER_ROLE,
                "reader-secret",
            ),
        }
        self.assertFalse(runtime.production_read_authority_enabled(base))

        enabled = {
            **base,
            "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "enabled",
        }
        self.assertTrue(runtime.production_read_authority_enabled(enabled))

        for environment in (
            {**enabled, "VERCEL_ENV": "preview"},
            {
                **enabled,
                "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
            },
            {
                **enabled,
                "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "true",
            },
            {
                **enabled,
                "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "ENABLED",
            },
        ):
            with self.subTest(environment=environment):
                self.assertFalse(
                    runtime.production_read_authority_enabled(environment)
                )

    def test_active_write_is_preview_only_and_requires_both_roles(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_write",
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                "cuevion_preview_mailbox_reader_v1",
                "reader-secret",
            ),
            "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                "cuevion_preview_mailbox_writer_v1",
                "writer-secret",
            ),
        }
        config = runtime.parse_mailbox_runtime_configuration(environment)
        self.assertIs(config.mode, runtime.MailboxRuntimeMode.ACTIVE_WRITE)
        self.assertEqual(
            config.reader_database_url.role,
            "cuevion_preview_mailbox_reader_v1",
        )
        self.assertEqual(
            config.writer_database_url.role,
            "cuevion_preview_mailbox_writer_v1",
        )

        missing_writer = dict(environment)
        missing_writer.pop("CUEVION_MAILBOX_WRITER_DATABASE_URL")
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(missing_writer)

        production = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_write",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                _READER_ROLE,
                "reader-secret",
            ),
            "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                _WRITER_ROLE,
                "writer-secret",
            ),
        }
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(production)

    def test_shadow_requires_nonempty_exact_production_roles(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(_READER_ROLE, "reader-secret"),
            "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(_WRITER_ROLE, "writer-secret"),
        }
        config = runtime.parse_mailbox_runtime_configuration(environment)
        self.assertIs(config.mode, runtime.MailboxRuntimeMode.SHADOW)
        self.assertEqual(config.reader_database_url.role, _READER_ROLE)
        self.assertEqual(config.writer_database_url.role, _WRITER_ROLE)

        invalid = dict(environment)
        invalid["CUEVION_MAILBOX_READER_DATABASE_URL"] = _url(_READER_ROLE, "")
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(invalid)

        invalid = dict(environment)
        invalid["CUEVION_MAILBOX_WRITER_DATABASE_URL"] = _url(
            "cuevion_auth_writer",
            "writer-secret",
        )
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(invalid)

    def test_shadow_requires_same_neon_endpoint_and_database(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(_READER_ROLE, "reader-secret"),
            "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                _WRITER_ROLE,
                "writer-secret",
                host="ep-other-pooler.c-4.eu-central-1.aws.neon.tech",
            ),
        }
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            runtime.parse_mailbox_runtime_configuration(environment)

    def test_database_url_repr_is_redacted(self):
        config = runtime.parse_mailbox_runtime_configuration(
            {
                "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
                "VERCEL_ENV": "production",
                "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                    _READER_ROLE, "reader-secret"
                ),
                "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                    _WRITER_ROLE, "writer-secret"
                ),
            }
        )
        self.assertEqual(
            repr(config.reader_database_url),
            "MailboxDatabaseUrl(<redacted>)",
        )
        self.assertNotIn("reader-secret", repr(config.reader_database_url))

    def test_preview_has_separate_role_namespace(self):
        reader = _url(
            "cuevion_preview_mailbox_reader_v1",
            "reader-secret",
        )
        writer = _url(
            "cuevion_preview_mailbox_writer_v1",
            "writer-secret",
        )
        config = runtime.parse_mailbox_runtime_configuration(
            {
                "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
                "VERCEL_ENV": "preview",
                "CUEVION_MAILBOX_READER_DATABASE_URL": reader,
                "CUEVION_MAILBOX_WRITER_DATABASE_URL": writer,
            }
        )
        self.assertEqual(
            config.reader_database_url.role,
            "cuevion_preview_mailbox_reader_v1",
        )


class MailboxConnectionFactoryTests(unittest.TestCase):
    def _database_url(self, role: str) -> runtime.MailboxDatabaseUrl:
        config = runtime.parse_mailbox_runtime_configuration(
            {
                "CUEVION_MAILBOX_POSTGRES_MODE": "shadow",
                "VERCEL_ENV": "production",
                "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                    _READER_ROLE, "reader-secret"
                ),
                "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                    _WRITER_ROLE, "writer-secret"
                ),
            }
        )
        if role == _READER_ROLE:
            return config.reader_database_url
        return config.writer_database_url

    def test_reader_connection_is_tls_bound_and_transaction_read_only(self):
        connection = _Connection(user=_READER_ROLE)
        connector = _Connector(connection)
        factory = runtime.MailboxConnectionFactory(
            self._database_url(_READER_ROLE),
            read_only=True,
            connect=connector,
        )
        self.assertIs(factory(), connection)
        self.assertEqual(connection.sql, ["SET TRANSACTION READ ONLY"])
        self.assertEqual(len(connector.calls), 1)
        self.assertFalse(connector.calls[0][1])
        self.assertEqual(connector.calls[0][2], 5)

    def test_writer_connection_does_not_force_read_only(self):
        connection = _Connection(user=_WRITER_ROLE)
        factory = runtime.MailboxConnectionFactory(
            self._database_url(_WRITER_ROLE),
            read_only=False,
            connect=_Connector(connection),
        )
        self.assertIs(factory(), connection)
        self.assertEqual(connection.sql, [])

    def test_wrong_server_user_or_tls_fails_closed_and_closes(self):
        wrong_user = _Connection(user="cuevion_auth_writer")
        factory = runtime.MailboxConnectionFactory(
            self._database_url(_READER_ROLE),
            read_only=True,
            connect=_Connector(wrong_user),
        )
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            factory()
        self.assertTrue(wrong_user.closed)

        no_tls = _Connection(user=_READER_ROLE, ssl_in_use=False)
        factory = runtime.MailboxConnectionFactory(
            self._database_url(_READER_ROLE),
            read_only=True,
            connect=_Connector(no_tls),
        )
        with self.assertRaises(runtime.MailboxRuntimeConfigurationError):
            factory()
        self.assertTrue(no_tls.closed)

    def test_active_read_builder_requires_explicit_preview_mode(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_read",
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                "cuevion_preview_mailbox_reader_v1",
                "reader-secret",
            ),
        }
        reader = runtime.build_active_read_mailbox_reader(environment)
        self.assertIsInstance(
            reader,
            runtime.PostgreSQLMailboxReaderRepository,
        )

        with self.assertRaises(runtime.MailboxRuntimeDisabledError):
            runtime.build_active_read_mailbox_reader({})

    def test_production_read_builder_requires_double_gate_and_is_reader_only(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "production_read",
            "CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY": "enabled",
            "VERCEL_ENV": "production",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                _READER_ROLE,
                "reader-secret",
            ),
        }
        connection = _Connection(user=_READER_ROLE)
        reader = runtime.build_production_read_mailbox_reader(
            environment,
            connect=_Connector(connection),
        )
        self.assertIsInstance(
            reader,
            runtime.PostgreSQLMailboxReaderRepository,
        )
        delegate_factory = reader._delegate._connection_factory
        created_connection = delegate_factory()
        self.assertIs(created_connection, connection)
        self.assertEqual(connection.sql, ["SET TRANSACTION READ ONLY"])

        missing_gate = dict(environment)
        missing_gate.pop("CUEVION_MAILBOX_PRODUCTION_READ_AUTHORITY")
        with self.assertRaises(runtime.MailboxRuntimeDisabledError):
            runtime.build_production_read_mailbox_reader(missing_gate)

        wrong_mode = dict(environment)
        wrong_mode["CUEVION_MAILBOX_POSTGRES_MODE"] = "shadow"
        with self.assertRaises(runtime.MailboxRuntimeDisabledError):
            runtime.build_production_read_mailbox_reader(wrong_mode)

    def test_active_write_builder_requires_explicit_preview_mode(self):
        environment = {
            "CUEVION_MAILBOX_POSTGRES_MODE": "active_write",
            "VERCEL_ENV": "preview",
            "CUEVION_MAILBOX_READER_DATABASE_URL": _url(
                "cuevion_preview_mailbox_reader_v1",
                "reader-secret",
            ),
            "CUEVION_MAILBOX_WRITER_DATABASE_URL": _url(
                "cuevion_preview_mailbox_writer_v1",
                "writer-secret",
            ),
        }
        repositories = runtime.build_active_write_mailbox_repositories(
            environment
        )
        self.assertIsInstance(
            repositories.reader,
            runtime.PostgreSQLMailboxReaderRepository,
        )
        self.assertIsInstance(
            repositories.writer,
            runtime.PostgreSQLMailboxRepository,
        )

        with self.assertRaises(runtime.MailboxRuntimeDisabledError):
            runtime.build_active_write_mailbox_repositories({})

    def test_shadow_builder_refuses_disabled_mode(self):
        with self.assertRaises(runtime.MailboxRuntimeDisabledError):
            runtime.build_shadow_mailbox_repositories({})


class MailboxRuntimeStaticTests(unittest.TestCase):
    def test_module_does_not_read_process_environment_or_expose_network_clients(self):
        source = _RUNTIME.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("os", imported)
        self.assertNotIn("socket", imported)
        self.assertNotIn("requests", imported)
        self.assertNotIn("httpx", imported)
        self.assertNotIn("os.environ", source)


if __name__ == "__main__":
    unittest.main()
