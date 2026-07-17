"""Pure, secret-safe configuration for the inactive database foundation."""

from collections.abc import Mapping as _Mapping
from enum import Enum as _Enum
from enum import EnumMeta as _EnumMeta
import re as _re
from unicodedata import category as _unicode_category
from urllib.parse import parse_qsl as _parse_qsl
from urllib.parse import quote as _quote
from urllib.parse import unquote as _unquote
from urllib.parse import urlencode as _urlencode
from urllib.parse import urlsplit as _urlsplit
from urllib.parse import urlunsplit as _urlunsplit


__all__ = (
    "DatabaseConfigurationError",
    "DatabaseTarget",
    "RuntimeDatabaseUrl",
    "MigrationDatabaseUrl",
    "DatabaseConfiguration",
    "parse_database_configuration",
)


class DatabaseConfigurationError(ValueError):
    """One fixed failure that never retains rejected configuration values."""

    __slots__ = ()

    def __new__(
        cls, *_arguments: object, **_keywords: object
    ) -> "DatabaseConfigurationError":
        return ValueError.__new__(cls)

    def __init__(self, *_arguments: object, **_keywords: object) -> None:
        ValueError.__init__(self)

    def __str__(self) -> str:
        return "invalid database configuration"

    def __repr__(self) -> str:
        return "DatabaseConfigurationError()"


def _raise_configuration_error() -> None:
    error = DatabaseConfigurationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_ENUM_MISSING = object()


class _ClosedTargetMeta(_EnumMeta):
    def __call__(
        cls,
        value: object = _ENUM_MISSING,
        *_arguments: object,
        **_keywords: object,
    ) -> object:
        if not _arguments and not _keywords:
            if type(value) is cls:
                return value
            if type(value) is str:
                member = cls._value2member_map_.get(value, _ENUM_MISSING)
                if type(member) is cls:
                    return member
        _raise_configuration_error()


class DatabaseTarget(str, _Enum, metaclass=_ClosedTargetMeta):
    PRODUCTION = "production"
    PREVIEW = "preview"


_CONSTRUCTION_TOKEN = object()
_PARSE_FAILED = object()
_RECORD_DEFINITION_OPEN = True
_INVALID_PERCENT_ESCAPE = _re.compile(r"%(?![0-9A-Fa-f]{2})")
_DNS_LABEL = _re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_VARIABLES = (
    "CUEVION_DATABASE_URL",
    "CUEVION_DATABASE_URL_UNPOOLED",
    "CUEVION_DATABASE_TARGET",
    "VERCEL_ENV",
    "PSYCOPG_IMPL",
)


class _SecretRecord:
    __slots__ = ()

    def __init_subclass__(cls, **keywords: object) -> None:
        if not _RECORD_DEFINITION_OPEN:
            _raise_configuration_error()
        super().__init_subclass__(**keywords)

    def __setattr__(self, _name: str, _value: object) -> None:
        _raise_configuration_error()

    def __delattr__(self, _name: str) -> None:
        _raise_configuration_error()

    def __reduce__(self) -> object:
        _raise_configuration_error()

    def __reduce_ex__(self, _protocol: object) -> object:
        _raise_configuration_error()

    def __getstate__(self) -> object:
        _raise_configuration_error()

    def __setstate__(self, _state: object) -> None:
        _raise_configuration_error()

    def __copy__(self) -> "_SecretRecord":
        return self

    def __deepcopy__(self, _memo: object) -> "_SecretRecord":
        return self


class RuntimeDatabaseUrl(_SecretRecord):
    __slots__ = ("_value", "_database", "_endpoint")

    def __init__(
        self,
        token: object,
        value: str,
        database: str,
        endpoint: str,
    ) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            _raise_configuration_error()
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_database", database)
        object.__setattr__(self, "_endpoint", endpoint)

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_value")

    def __repr__(self) -> str:
        return "RuntimeDatabaseUrl(<redacted>)"

    __str__ = __repr__


class MigrationDatabaseUrl(_SecretRecord):
    __slots__ = ("_value", "_database", "_endpoint")

    def __init__(
        self,
        token: object,
        value: str,
        database: str,
        endpoint: str,
    ) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            _raise_configuration_error()
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_database", database)
        object.__setattr__(self, "_endpoint", endpoint)

    @property
    def value(self) -> str:
        return object.__getattribute__(self, "_value")

    def __repr__(self) -> str:
        return "MigrationDatabaseUrl(<redacted>)"

    __str__ = __repr__


class DatabaseConfiguration(_SecretRecord):
    __slots__ = ("_target", "_runtime_url", "_migration_url")

    def __init__(
        self,
        token: object,
        target: DatabaseTarget,
        runtime_url: RuntimeDatabaseUrl,
        migration_url: MigrationDatabaseUrl,
    ) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            _raise_configuration_error()
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_runtime_url", runtime_url)
        object.__setattr__(self, "_migration_url", migration_url)

    @property
    def target(self) -> DatabaseTarget:
        return object.__getattribute__(self, "_target")

    @property
    def runtime_url(self) -> RuntimeDatabaseUrl:
        return object.__getattribute__(self, "_runtime_url")

    @property
    def migration_url(self) -> MigrationDatabaseUrl:
        return object.__getattribute__(self, "_migration_url")

    def __repr__(self) -> str:
        return "DatabaseConfiguration(<redacted>)"

    __str__ = __repr__


_RECORD_DEFINITION_OPEN = False


def _clean_exact_text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(_unicode_category(character) == "Cc" for character in value)
    ):
        _raise_configuration_error()
    return value


def _decode_component(value: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(value) is not None:
        _raise_configuration_error()
    try:
        decoded = _unquote(value, encoding="utf-8", errors="strict")
    except Exception:
        _raise_configuration_error()
    return _clean_exact_text(decoded)


def _parse_url(value: object, *, pooled: bool) -> tuple[str, str, str, int]:
    raw = _clean_exact_text(value)
    scheme, separator, _remainder = raw.partition("://")
    if separator != "://" or scheme not in ("postgresql", "postgresql+psycopg"):
        _raise_configuration_error()
    try:
        parsed = _urlsplit(raw)
        port = parsed.port
    except Exception:
        _raise_configuration_error()
    if (
        parsed.scheme != scheme
        or parsed.fragment
        or not parsed.netloc
        or parsed.netloc.count("@") != 1
        or parsed.netloc.partition("@")[0].count(":") != 1
        or _INVALID_PERCENT_ESCAPE.search(parsed.query) is not None
    ):
        _raise_configuration_error()
    if parsed.username is None or parsed.password is None or parsed.hostname is None:
        _raise_configuration_error()
    username = _decode_component(parsed.username)
    password = _decode_component(parsed.password)
    if "%" in parsed.hostname:
        _raise_configuration_error()
    hostname = _decode_component(parsed.hostname)
    if not username or not password or not hostname:
        _raise_configuration_error()
    if parsed.path.count("/") != 1 or not parsed.path.startswith("/"):
        _raise_configuration_error()
    database = _decode_component(parsed.path[1:])
    if not database or "/" in database:
        _raise_configuration_error()
    endpoint = hostname.casefold()
    endpoint_labels = endpoint.split(".")
    if (
        hostname != endpoint
        or not endpoint.isascii()
        or len(endpoint) > 253
        or any(_DNS_LABEL.fullmatch(label) is None for label in endpoint_labels)
        or not endpoint.endswith(".neon.tech")
        or not endpoint_labels[0].startswith("ep-")
    ):
        _raise_configuration_error()
    marker_count = endpoint.count("-pooler")
    endpoint_label = endpoint_labels[0]
    if pooled:
        if marker_count != 1 or not endpoint_label.endswith("-pooler"):
            _raise_configuration_error()
    elif marker_count != 0:
        _raise_configuration_error()
    try:
        query_items = _parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=2,
        )
    except Exception:
        _raise_configuration_error()
    if len({key for key, _value in query_items}) != len(query_items):
        _raise_configuration_error()
    query = {}
    for key, query_value in query_items:
        clean_key = _clean_exact_text(key)
        clean_value = _clean_exact_text(query_value)
        if clean_key not in ("sslmode", "channel_binding"):
            _raise_configuration_error()
        query[clean_key] = clean_value
    if query.get("sslmode") != "require":
        _raise_configuration_error()
    if "channel_binding" in query and query["channel_binding"] != "require":
        _raise_configuration_error()
    normalized_query = _urlencode(
        tuple(
            (key, query[key])
            for key in ("sslmode", "channel_binding")
            if key in query
        )
    )
    if port is not None and not 1 <= port <= 65_535:
        _raise_configuration_error()
    effective_port = 5_432 if port is None else port
    normalized_netloc = (
        f"{_quote(username, safe='')}:{_quote(password, safe='')}@{endpoint}"
        + ("" if port is None else f":{port}")
    )
    normalized = _urlunsplit(
        (
            "postgresql+psycopg",
            normalized_netloc,
            "/" + _quote(database, safe=""),
            normalized_query,
            "",
        )
    )
    del raw, username, password, port, normalized_netloc
    return normalized, database, endpoint, effective_port


def _parse_configuration_worker(
    environment: _Mapping[str, str],
) -> object:
    values: dict[str, str] = {}
    try:
        if not isinstance(environment, _Mapping):
            _raise_configuration_error()
        for name in _VARIABLES:
            value = environment[name]
            values[name] = _clean_exact_text(value)
        target = DatabaseTarget(values["CUEVION_DATABASE_TARGET"])
        vercel_environment = values["VERCEL_ENV"]
        if vercel_environment == "development" or vercel_environment != target.value:
            _raise_configuration_error()
        if values["PSYCOPG_IMPL"] != "binary":
            _raise_configuration_error()
        (
            runtime_value,
            runtime_database,
            runtime_endpoint,
            runtime_port,
        ) = _parse_url(values["CUEVION_DATABASE_URL"], pooled=True)
        (
            migration_value,
            migration_database,
            migration_endpoint,
            migration_port,
        ) = _parse_url(values["CUEVION_DATABASE_URL_UNPOOLED"], pooled=False)
        if (
            runtime_database != migration_database
            or runtime_endpoint.replace("-pooler", "", 1) != migration_endpoint
            or runtime_port != migration_port
        ):
            _raise_configuration_error()
        runtime = RuntimeDatabaseUrl(
            _CONSTRUCTION_TOKEN,
            runtime_value,
            runtime_database,
            runtime_endpoint,
        )
        migration = MigrationDatabaseUrl(
            _CONSTRUCTION_TOKEN,
            migration_value,
            migration_database,
            migration_endpoint,
        )
        result: object = DatabaseConfiguration(
            _CONSTRUCTION_TOKEN, target, runtime, migration
        )
    except Exception:
        result = _PARSE_FAILED
    return result


def parse_database_configuration(
    environment: _Mapping[str, str],
) -> DatabaseConfiguration:
    """Parse caller-supplied values without reading process environment state."""

    result = _parse_configuration_worker(environment)
    if type(result) is DatabaseConfiguration:
        return result
    del environment, result
    _raise_configuration_error()
