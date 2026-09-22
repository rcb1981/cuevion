"""Runtime composition for controlled durable mailbox PostgreSQL reads.

This module is deliberately outside `api/`: importing or deploying it exposes no
route. `active_read` is Preview-only and constructs a reader repository only.
Active writes are Preview-only; Production activation remains unavailable.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import unquote, urlsplit

from cuevion_mailbox.postgresql_repository import (
    PostgreSQLMailboxReaderRepository,
    PostgreSQLMailboxRepository,
)


_MODE_VARIABLE = "CUEVION_MAILBOX_POSTGRES_MODE"
_READER_URL_VARIABLE = "CUEVION_MAILBOX_READER_DATABASE_URL"
_WRITER_URL_VARIABLE = "CUEVION_MAILBOX_WRITER_DATABASE_URL"
_MAX_DATABASE_URL_CHARACTERS = 8_192
_CONNECT_TIMEOUT_SECONDS = 5
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DATABASE_URL_TOKEN = object()


class MailboxRuntimeMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    ACTIVE_READ = "active_read"
    ACTIVE_WRITE = "active_write"


class MailboxRuntimeConfigurationError(RuntimeError):
    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object):
        if cls is not MailboxRuntimeConfigurationError:
            raise TypeError("mailbox runtime configuration errors are closed")
        del args, kwargs
        return RuntimeError.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __repr__(self) -> str:
        return "MailboxRuntimeConfigurationError()"

    __str__ = __repr__


class MailboxRuntimeDisabledError(RuntimeError):
    __slots__ = ()

    def __new__(cls, *args: object, **kwargs: object):
        if cls is not MailboxRuntimeDisabledError:
            raise TypeError("mailbox runtime disabled errors are closed")
        del args, kwargs
        return RuntimeError.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __repr__(self) -> str:
        return "MailboxRuntimeDisabledError()"

    __str__ = __repr__


def _configuration_error() -> None:
    error = MailboxRuntimeConfigurationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


class MailboxDatabaseUrl:
    """Parser-controlled database URL whose display is always redacted."""

    __slots__ = ("_value", "_role", "_hostname", "_database")

    def __init__(
        self,
        token: object,
        value: str,
        role: str,
        hostname: str,
        database: str,
    ) -> None:
        if token is not _DATABASE_URL_TOKEN:
            _configuration_error()
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_hostname", hostname)
        object.__setattr__(self, "_database", database)

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_value")

    @property
    def role(self) -> str:
        return object.__getattribute__(self, "_role")

    @property
    def hostname(self) -> str:
        return object.__getattribute__(self, "_hostname")

    @property
    def database(self) -> str:
        return object.__getattribute__(self, "_database")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        _configuration_error()

    def __delattr__(self, name: str) -> None:
        del name
        _configuration_error()

    def __repr__(self) -> str:
        return "MailboxDatabaseUrl(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> object:
        _configuration_error()

    def __reduce_ex__(self, protocol: object) -> object:
        del protocol
        _configuration_error()


@dataclass(frozen=True, slots=True)
class MailboxRuntimeConfiguration:
    mode: MailboxRuntimeMode
    vercel_environment: str | None
    reader_database_url: MailboxDatabaseUrl | None
    writer_database_url: MailboxDatabaseUrl | None


def _clean_text(value: object, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        _configuration_error()
    return value


def _decode_url_component(value: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(value) is not None:
        _configuration_error()
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except Exception:
        _configuration_error()
    return _clean_text(decoded, maximum=_MAX_DATABASE_URL_CHARACTERS)


def _expected_roles(vercel_environment: str) -> tuple[str, str]:
    if vercel_environment == "production":
        return (
            "cuevion_production_mailbox_reader_v1",
            "cuevion_production_mailbox_writer_v1",
        )
    if vercel_environment == "preview":
        return (
            "cuevion_preview_mailbox_reader_v1",
            "cuevion_preview_mailbox_writer_v1",
        )
    _configuration_error()


def _parse_database_url(value: object, *, expected_role: str) -> MailboxDatabaseUrl:
    raw = _clean_text(value, maximum=_MAX_DATABASE_URL_CHARACTERS)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except Exception:
        _configuration_error()
    if (
        parsed.scheme != "postgresql"
        or parsed.fragment
        or not parsed.netloc
        or parsed.netloc.count("@") != 1
        or parsed.netloc.partition("@")[0].count(":") != 1
        or parsed.username is None
        or parsed.password is None
        or parsed.hostname is None
    ):
        _configuration_error()

    role = _decode_url_component(parsed.username)
    password = _decode_url_component(parsed.password)
    if role != expected_role or not password:
        _configuration_error()

    if "%" in parsed.hostname:
        _configuration_error()
    hostname = _clean_text(parsed.hostname, maximum=253).casefold()
    if (
        not hostname.isascii()
        or any(character.isspace() for character in hostname)
        or not hostname.endswith(".neon.tech")
        or "-pooler." not in hostname
    ):
        _configuration_error()
    if port not in (None, 5432):
        _configuration_error()

    if parsed.path.count("/") != 1 or not parsed.path.startswith("/"):
        _configuration_error()
    database = _decode_url_component(parsed.path[1:])
    if "/" in database:
        _configuration_error()

    query_parts = parsed.query.split("&")
    if set(query_parts) != {
        "sslmode=require",
        "channel_binding=require",
    } or len(query_parts) != 2:
        _configuration_error()

    return MailboxDatabaseUrl(
        _DATABASE_URL_TOKEN,
        raw,
        role,
        hostname,
        database,
    )


def parse_mailbox_runtime_configuration(
    environment: Mapping[str, str],
) -> MailboxRuntimeConfiguration:
    """Parse caller-supplied configuration without reading process environment."""

    if not isinstance(environment, Mapping):
        _configuration_error()

    raw_mode = environment.get(_MODE_VARIABLE, MailboxRuntimeMode.DISABLED.value)
    try:
        mode = MailboxRuntimeMode(raw_mode)
    except Exception:
        _configuration_error()

    if mode is MailboxRuntimeMode.DISABLED:
        return MailboxRuntimeConfiguration(mode, None, None, None)

    vercel_environment = _clean_text(environment.get("VERCEL_ENV"), maximum=32)
    expected_reader, expected_writer = _expected_roles(vercel_environment)
    reader = _parse_database_url(
        environment.get(_READER_URL_VARIABLE),
        expected_role=expected_reader,
    )

    if mode is MailboxRuntimeMode.ACTIVE_READ:
        if vercel_environment != "preview":
            _configuration_error()
        return MailboxRuntimeConfiguration(
            mode,
            vercel_environment,
            reader,
            None,
        )

    writer = _parse_database_url(
        environment.get(_WRITER_URL_VARIABLE),
        expected_role=expected_writer,
    )
    if mode is MailboxRuntimeMode.ACTIVE_WRITE and vercel_environment != "preview":
        _configuration_error()
    if (
        reader.hostname != writer.hostname
        or reader.database != writer.database
    ):
        _configuration_error()

    return MailboxRuntimeConfiguration(
        mode,
        vercel_environment,
        reader,
        writer,
    )


class _ConnectCallable(Protocol):
    def __call__(
        self,
        conninfo: str,
        *,
        autocommit: bool,
        connect_timeout: int,
    ) -> object:
        ...


def _default_connect(
    conninfo: str,
    *,
    autocommit: bool,
    connect_timeout: int,
) -> object:
    import psycopg

    return psycopg.connect(
        conninfo,
        autocommit=autocommit,
        connect_timeout=connect_timeout,
    )


class MailboxConnectionFactory:
    """Fresh TLS-proven connection bound to one exact mailbox runtime role."""

    __slots__ = ("_database_url", "_connect", "_read_only")

    def __init__(
        self,
        database_url: MailboxDatabaseUrl,
        *,
        read_only: bool,
        connect: _ConnectCallable | None = None,
    ) -> None:
        if type(database_url) is not MailboxDatabaseUrl or type(read_only) is not bool:
            _configuration_error()
        if connect is not None and not callable(connect):
            _configuration_error()
        object.__setattr__(self, "_database_url", database_url)
        object.__setattr__(self, "_connect", connect)
        object.__setattr__(self, "_read_only", read_only)

    def __repr__(self) -> str:
        return "MailboxConnectionFactory(<redacted>)"

    __str__ = __repr__

    def __call__(self) -> object:
        database_url = object.__getattribute__(self, "_database_url")
        connector = object.__getattribute__(self, "_connect")
        connection = (
            _default_connect if connector is None else connector
        )(
            database_url.value,
            autocommit=False,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        cursor = None
        try:
            if getattr(connection, "autocommit") is not False:
                raise MailboxRuntimeConfigurationError()
            pgconn = getattr(connection, "pgconn")
            if getattr(pgconn, "ssl_in_use") is not True:
                raise MailboxRuntimeConfigurationError()
            info = getattr(connection, "info")
            if (
                getattr(info, "user") != database_url.role
                or getattr(info, "dbname") != database_url.database
            ):
                raise MailboxRuntimeConfigurationError()
            if object.__getattribute__(self, "_read_only") is True:
                cursor = getattr(connection, "cursor")()
                getattr(cursor, "execute")("SET TRANSACTION READ ONLY")
                getattr(cursor, "close")()
                cursor = None
        except BaseException:
            if cursor is not None:
                try:
                    getattr(cursor, "close")()
                except BaseException:
                    pass
            try:
                getattr(connection, "close")()
            except BaseException:
                pass
            raise
        return connection


def build_active_read_mailbox_reader(
    environment: Mapping[str, str],
    *,
    connect: _ConnectCallable | None = None,
) -> PostgreSQLMailboxReaderRepository:
    """Compose the bounded Preview-only active reader repository."""

    config = parse_mailbox_runtime_configuration(environment)
    if config.mode is not MailboxRuntimeMode.ACTIVE_READ:
        raise MailboxRuntimeDisabledError()
    reader_url = config.reader_database_url
    if type(reader_url) is not MailboxDatabaseUrl:
        _configuration_error()
    return PostgreSQLMailboxReaderRepository(
        MailboxConnectionFactory(
            reader_url,
            read_only=True,
            connect=connect,
        )
    )


@dataclass(frozen=True, slots=True)
class ActiveWriteMailboxRepositories:
    reader: PostgreSQLMailboxReaderRepository
    writer: PostgreSQLMailboxRepository


def build_active_write_mailbox_repositories(
    environment: Mapping[str, str],
    *,
    connect: _ConnectCallable | None = None,
) -> ActiveWriteMailboxRepositories:
    """Compose Preview-only active reader/writer repositories."""

    config = parse_mailbox_runtime_configuration(environment)
    if config.mode is not MailboxRuntimeMode.ACTIVE_WRITE:
        raise MailboxRuntimeDisabledError()
    reader_url = config.reader_database_url
    writer_url = config.writer_database_url
    if (
        type(reader_url) is not MailboxDatabaseUrl
        or type(writer_url) is not MailboxDatabaseUrl
    ):
        _configuration_error()

    reader_factory = MailboxConnectionFactory(
        reader_url,
        read_only=True,
        connect=connect,
    )
    writer_factory = MailboxConnectionFactory(
        writer_url,
        read_only=False,
        connect=connect,
    )
    return ActiveWriteMailboxRepositories(
        PostgreSQLMailboxReaderRepository(reader_factory),
        PostgreSQLMailboxRepository(writer_factory),
    )


@dataclass(frozen=True, slots=True)
class ShadowMailboxRepositories:
    reader: PostgreSQLMailboxReaderRepository
    writer: PostgreSQLMailboxRepository


def build_shadow_mailbox_repositories(
    environment: Mapping[str, str],
    *,
    connect: _ConnectCallable | None = None,
) -> ShadowMailboxRepositories:
    """Compose repositories only for explicit shadow validation.

    No route in this slice calls this function.
    """

    config = parse_mailbox_runtime_configuration(environment)
    if config.mode is not MailboxRuntimeMode.SHADOW:
        raise MailboxRuntimeDisabledError()
    reader_url = config.reader_database_url
    writer_url = config.writer_database_url
    if (
        type(reader_url) is not MailboxDatabaseUrl
        or type(writer_url) is not MailboxDatabaseUrl
    ):
        _configuration_error()

    reader_factory = MailboxConnectionFactory(
        reader_url,
        read_only=True,
        connect=connect,
    )
    writer_factory = MailboxConnectionFactory(
        writer_url,
        read_only=False,
        connect=connect,
    )
    return ShadowMailboxRepositories(
        PostgreSQLMailboxReaderRepository(reader_factory),
        PostgreSQLMailboxRepository(writer_factory),
    )


__all__ = (
    "ActiveWriteMailboxRepositories",
    "MailboxConnectionFactory",
    "MailboxDatabaseUrl",
    "MailboxRuntimeConfiguration",
    "MailboxRuntimeConfigurationError",
    "MailboxRuntimeDisabledError",
    "MailboxRuntimeMode",
    "ShadowMailboxRepositories",
    "build_active_read_mailbox_reader",
    "build_active_write_mailbox_repositories",
    "build_shadow_mailbox_repositories",
    "parse_mailbox_runtime_configuration",
)
