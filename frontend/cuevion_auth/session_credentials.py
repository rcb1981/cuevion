"""Inactive production-session credential parsing and derivation boundary.

This provider-independent module validates only explicitly injected key
configuration and untrusted duplicate-preserving raw headers.  It performs no
request handling, credential resolution, storage, environment access, session
lifecycle work, or feature activation.
"""

import sys as _sys

if (
    __name__ != "cuevion_auth.session_credentials"
    or __package__ != "cuevion_auth"
):
    raise ImportError("session credentials require their canonical import identity")
if (
    getattr(
        _sys.modules.get("cuevion_auth.session_credentials"),
        "__dict__",
        None,
    )
    is not globals()
):
    raise ImportError("session credentials require their canonical module object")
if "_AUTH_B1A_SESSION_CREDENTIALS_INITIALIZED" in globals():
    raise ImportError("session credentials cannot be initialized more than once")
_AUTH_B1A_SESSION_CREDENTIALS_INITIALIZED = True

import base64 as _base64
import binascii as _binascii
import hashlib as _hashlib
import hmac as _hmac


__all__ = (
    "SessionKeyConfigurationError",
    "SessionKeyConfiguration",
    "DerivedSessionCredential",
    "parse_session_key_configuration",
    "derive_request_session_credential",
)


_SESSION_COOKIE_NAME = "__Host-cuevion_session"
_SESSION_VERSION = "v1"
_LOOKUP_DOMAIN = b"cuevion/auth/session-lookup/v1\x00"
_BINDING_DOMAIN = b"cuevion/auth/session-binding/v1\x00"

_MAX_HEADER_PAIRS = 64
_MAX_HEADER_NAME_CHARACTERS = 128
_MAX_HEADER_VALUE_BYTES = 8_192
_MAX_TOTAL_HEADER_BYTES = 32_768
_MAX_COOKIE_HEADER_BYTES = 8_192
_MAX_CREDENTIAL_BYTES = 128
_MAX_EPOCH = 2_147_483_647
_MAX_UINT32 = 4_294_967_295
_ENCODED_KEY_OR_SECRET_LENGTH = 43

_TOKEN_CHARACTERS = frozenset(
    "!#$%&'*+-.^_`|~"
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
)
_BASE64URL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)

_LOOKUP_CURRENT_EPOCH = "lookup_current_epoch"
_LOOKUP_CURRENT_KEY = "lookup_current_key"
_LOOKUP_PREVIOUS_EPOCH = "lookup_previous_epoch"
_LOOKUP_PREVIOUS_KEY = "lookup_previous_key"
_BINDING_CURRENT_EPOCH = "binding_current_epoch"
_BINDING_CURRENT_KEY = "binding_current_key"
_BINDING_PREVIOUS_EPOCH = "binding_previous_epoch"
_BINDING_PREVIOUS_KEY = "binding_previous_key"

_REQUIRED_CONFIGURATION_KEYS = frozenset(
    {
        _LOOKUP_CURRENT_EPOCH,
        _LOOKUP_CURRENT_KEY,
        _BINDING_CURRENT_EPOCH,
        _BINDING_CURRENT_KEY,
    }
)
_ALLOWED_CONFIGURATION_KEYS = frozenset(
    {
        *_REQUIRED_CONFIGURATION_KEYS,
        _LOOKUP_PREVIOUS_EPOCH,
        _LOOKUP_PREVIOUS_KEY,
        _BINDING_PREVIOUS_EPOCH,
        _BINDING_PREVIOUS_KEY,
    }
)


class SessionKeyConfigurationError(ValueError):
    """A fixed, value-free failure for invalid trusted key configuration."""

    __slots__ = ()

    def __new__(
        cls, *_arguments: object, **_keywords: object
    ) -> "SessionKeyConfigurationError":
        return ValueError.__new__(cls)

    def __init__(self, *_arguments: object, **_keywords: object) -> None:
        ValueError.__init__(self)

    @property
    def args(self) -> tuple[object, ...]:
        return ()

    @args.setter
    def args(self, _value: object) -> None:
        return None

    def __str__(self) -> str:
        return "session key configuration is invalid"

    def __repr__(self) -> str:
        return "SessionKeyConfigurationError()"


def _raise_configuration_error() -> None:
    """Raise a fresh fixed error without retaining an underlying exception."""

    error = SessionKeyConfigurationError()
    try:
        raise error
    finally:
        object.__setattr__(error, "__context__", None)
        object.__setattr__(error, "__cause__", None)


_CONFIGURATION_SENTINEL = object()
_DERIVED_CREDENTIAL_SENTINEL = object()
_CONFIGURATION_FAILURE = object()
_CONFIGURATION_CONSTRUCTION_ERROR = (
    "session key configurations are parser-controlled"
)
_CONFIGURATION_MUTATION_ERROR = "session key configurations are immutable"
_CONFIGURATION_PICKLE_ERROR = "session key configurations cannot be serialized"
_DERIVED_CONSTRUCTION_ERROR = "derived session credentials are factory-controlled"
_DERIVED_MUTATION_ERROR = "derived session credentials are immutable"
_DERIVED_PICKLE_ERROR = "derived session credentials cannot be serialized"


class SessionKeyConfiguration:
    """Opaque immutable snapshot of validated server-side key configuration."""

    __slots__ = (
        "_sentinel",
        "_lookup_current_epoch",
        "_lookup_current_key",
        "_lookup_previous_epoch",
        "_lookup_previous_key",
        "_binding_current_epoch",
        "_binding_current_key",
        "_binding_previous_epoch",
        "_binding_previous_key",
    )

    def __new__(
        cls, *_arguments: object, **_keywords: object
    ) -> "SessionKeyConfiguration":
        raise TypeError(_CONFIGURATION_CONSTRUCTION_ERROR)

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError(_CONFIGURATION_CONSTRUCTION_ERROR)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError(_CONFIGURATION_MUTATION_ERROR)

    def __delattr__(self, _name: str) -> None:
        raise AttributeError(_CONFIGURATION_MUTATION_ERROR)

    def __repr__(self) -> str:
        return "<SessionKeyConfiguration>"

    def __str__(self) -> str:
        return "SessionKeyConfiguration"

    def __copy__(self) -> "SessionKeyConfiguration":
        return self

    def __deepcopy__(self, _memo: object) -> "SessionKeyConfiguration":
        return self

    def __reduce__(self) -> object:
        raise TypeError(_CONFIGURATION_PICKLE_ERROR)

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError(_CONFIGURATION_PICKLE_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(_CONFIGURATION_PICKLE_ERROR)

    def __setstate__(self, _state: object) -> None:
        raise TypeError(_CONFIGURATION_PICKLE_ERROR)


class DerivedSessionCredential:
    """Opaque derived request credential containing no raw secret or key."""

    __slots__ = (
        "_sentinel",
        "_lookup_key_epoch",
        "_binding_key_epoch",
        "_credential_epoch",
        "_credential_lookup_digest",
        "_credential_binding_digest",
    )

    def __new__(
        cls, *_arguments: object, **_keywords: object
    ) -> "DerivedSessionCredential":
        raise TypeError(_DERIVED_CONSTRUCTION_ERROR)

    def __init_subclass__(cls, **_keywords: object) -> None:
        raise TypeError(_DERIVED_CONSTRUCTION_ERROR)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError(_DERIVED_MUTATION_ERROR)

    def __delattr__(self, _name: str) -> None:
        raise AttributeError(_DERIVED_MUTATION_ERROR)

    def __repr__(self) -> str:
        return "<DerivedSessionCredential>"

    def __str__(self) -> str:
        return "DerivedSessionCredential"

    def __copy__(self) -> "DerivedSessionCredential":
        return self

    def __deepcopy__(self, _memo: object) -> "DerivedSessionCredential":
        return self

    def __reduce__(self) -> object:
        raise TypeError(_DERIVED_PICKLE_ERROR)

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError(_DERIVED_PICKLE_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(_DERIVED_PICKLE_ERROR)

    def __setstate__(self, _state: object) -> None:
        raise TypeError(_DERIVED_PICKLE_ERROR)

    @property
    def lookup_key_epoch(self) -> int:
        return self._lookup_key_epoch

    @property
    def binding_key_epoch(self) -> int:
        return self._binding_key_epoch

    @property
    def credential_epoch(self) -> int:
        return self._credential_epoch

    @property
    def credential_lookup_digest(self) -> str:
        return self._credential_lookup_digest

    @property
    def credential_binding_digest(self) -> str:
        return self._credential_binding_digest


def _encode_base64url(value: bytes) -> str:
    return _base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_canonical_32_bytes(value: object) -> bytes | None:
    if (
        type(value) is not str
        or len(value) != _ENCODED_KEY_OR_SECRET_LENGTH
        or not value.isascii()
        or any(character not in _BASE64URL_CHARACTERS for character in value)
    ):
        return None
    try:
        decoded = _base64.b64decode(
            value.encode("ascii") + b"=",
            altchars=b"-_",
            validate=True,
        )
    except _binascii.Error:
        return None
    if (
        decoded is None
        or type(decoded) is not bytes
        or len(decoded) != 32
        or _encode_base64url(decoded) != value
    ):
        return None
    return decoded


def _parse_epoch(value: object) -> int | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 10
        or value[0] == "0"
        or not value.isascii()
        or any(character < "0" or character > "9" for character in value)
        or (len(value) == 10 and value > "2147483647")
    ):
        return None
    epoch = int(value)
    return epoch if 1 <= epoch <= _MAX_EPOCH else None


def _configuration_components_are_valid(
    lookup_current_epoch: object,
    lookup_current_key: object,
    lookup_previous_epoch: object,
    lookup_previous_key: object,
    binding_current_epoch: object,
    binding_current_key: object,
    binding_previous_epoch: object,
    binding_previous_key: object,
) -> bool:
    if (
        type(lookup_current_epoch) is not int
        or type(lookup_current_key) is not bytes
        or type(binding_current_epoch) is not int
        or type(binding_current_key) is not bytes
        or not (
            (lookup_previous_epoch is None and lookup_previous_key is None)
            or (
                type(lookup_previous_epoch) is int
                and type(lookup_previous_key) is bytes
            )
        )
        or not (
            (binding_previous_epoch is None and binding_previous_key is None)
            or (
                type(binding_previous_epoch) is int
                and type(binding_previous_key) is bytes
            )
        )
    ):
        return False
    epochs = (
        lookup_current_epoch,
        binding_current_epoch,
        *(
            ()
            if lookup_previous_epoch is None
            else (lookup_previous_epoch,)
        ),
        *(
            ()
            if binding_previous_epoch is None
            else (binding_previous_epoch,)
        ),
    )
    if any(epoch < 1 or epoch > _MAX_EPOCH for epoch in epochs):
        return False
    if (
        lookup_previous_epoch is not None
        and lookup_current_epoch <= lookup_previous_epoch
    ):
        return False
    if (
        binding_previous_epoch is not None
        and binding_current_epoch <= binding_previous_epoch
    ):
        return False
    keys = (
        lookup_current_key,
        binding_current_key,
        *(
            ()
            if lookup_previous_key is None
            else (lookup_previous_key,)
        ),
        *(
            ()
            if binding_previous_key is None
            else (binding_previous_key,)
        ),
    )
    return (
        all(len(key) == 32 for key in keys)
        and len(frozenset(keys)) == len(keys)
    )


def _new_configuration(
    lookup_current_epoch: int,
    lookup_current_key: bytes,
    lookup_previous_epoch: int | None,
    lookup_previous_key: bytes | None,
    binding_current_epoch: int,
    binding_current_key: bytes,
    binding_previous_epoch: int | None,
    binding_previous_key: bytes | None,
) -> SessionKeyConfiguration:
    configuration = object.__new__(SessionKeyConfiguration)
    copied_lookup_current_key = bytes(bytearray(lookup_current_key))
    copied_lookup_previous_key = (
        None
        if lookup_previous_key is None
        else bytes(bytearray(lookup_previous_key))
    )
    copied_binding_current_key = bytes(bytearray(binding_current_key))
    copied_binding_previous_key = (
        None
        if binding_previous_key is None
        else bytes(bytearray(binding_previous_key))
    )
    for name, value in (
        ("_sentinel", _CONFIGURATION_SENTINEL),
        ("_lookup_current_epoch", lookup_current_epoch),
        ("_lookup_current_key", copied_lookup_current_key),
        ("_lookup_previous_epoch", lookup_previous_epoch),
        ("_lookup_previous_key", copied_lookup_previous_key),
        ("_binding_current_epoch", binding_current_epoch),
        ("_binding_current_key", copied_binding_current_key),
        ("_binding_previous_epoch", binding_previous_epoch),
        ("_binding_previous_key", copied_binding_previous_key),
    ):
        object.__setattr__(configuration, name, value)
    return configuration


def _parse_session_key_configuration_worker(
    values: object,
) -> SessionKeyConfiguration | object:
    """Return one validated configuration or a non-sensitive failure sentinel."""

    try:
        if type(values) is not dict:
            return _CONFIGURATION_FAILURE
        items = tuple(dict.items(values))
        if any(
            type(key) is not str or type(value) is not str
            for key, value in items
        ):
            return _CONFIGURATION_FAILURE
        snapshot = {key: value for key, value in items}
        names = frozenset(snapshot)
        if (
            not _REQUIRED_CONFIGURATION_KEYS.issubset(names)
            or not names.issubset(_ALLOWED_CONFIGURATION_KEYS)
            or (
                (_LOOKUP_PREVIOUS_EPOCH in names)
                != (_LOOKUP_PREVIOUS_KEY in names)
            )
            or (
                (_BINDING_PREVIOUS_EPOCH in names)
                != (_BINDING_PREVIOUS_KEY in names)
            )
        ):
            return _CONFIGURATION_FAILURE

        has_lookup_previous = _LOOKUP_PREVIOUS_EPOCH in names
        has_binding_previous = _BINDING_PREVIOUS_EPOCH in names
        lookup_current_epoch = _parse_epoch(snapshot[_LOOKUP_CURRENT_EPOCH])
        lookup_previous_epoch = (
            _parse_epoch(snapshot[_LOOKUP_PREVIOUS_EPOCH])
            if has_lookup_previous
            else None
        )
        binding_current_epoch = _parse_epoch(snapshot[_BINDING_CURRENT_EPOCH])
        binding_previous_epoch = (
            _parse_epoch(snapshot[_BINDING_PREVIOUS_EPOCH])
            if has_binding_previous
            else None
        )
        lookup_current_key = _decode_canonical_32_bytes(
            snapshot[_LOOKUP_CURRENT_KEY]
        )
        lookup_previous_key = (
            _decode_canonical_32_bytes(snapshot[_LOOKUP_PREVIOUS_KEY])
            if has_lookup_previous
            else None
        )
        binding_current_key = _decode_canonical_32_bytes(
            snapshot[_BINDING_CURRENT_KEY]
        )
        binding_previous_key = (
            _decode_canonical_32_bytes(snapshot[_BINDING_PREVIOUS_KEY])
            if has_binding_previous
            else None
        )
        if (
            lookup_current_epoch is None
            or lookup_current_key is None
            or binding_current_epoch is None
            or binding_current_key is None
            or (
                has_lookup_previous
                and (
                    lookup_previous_epoch is None
                    or lookup_previous_key is None
                )
            )
            or (
                has_binding_previous
                and (
                    binding_previous_epoch is None
                    or binding_previous_key is None
                )
            )
            or not _configuration_components_are_valid(
                lookup_current_epoch,
                lookup_current_key,
                lookup_previous_epoch,
                lookup_previous_key,
                binding_current_epoch,
                binding_current_key,
                binding_previous_epoch,
                binding_previous_key,
            )
        ):
            return _CONFIGURATION_FAILURE
        configuration = _new_configuration(
            lookup_current_epoch,
            lookup_current_key,
            lookup_previous_epoch,
            lookup_previous_key,
            binding_current_epoch,
            binding_current_key,
            binding_previous_epoch,
            binding_previous_key,
        )
    except Exception:
        return _CONFIGURATION_FAILURE
    return (
        configuration
        if type(configuration) is SessionKeyConfiguration
        else _CONFIGURATION_FAILURE
    )


def parse_session_key_configuration(
    values: dict[str, str],
) -> SessionKeyConfiguration:
    """Validate one explicitly injected trusted key-configuration snapshot."""

    parsed_configuration = _parse_session_key_configuration_worker(values)
    del values
    if type(parsed_configuration) is SessionKeyConfiguration:
        return parsed_configuration
    del parsed_configuration
    _raise_configuration_error()


def _configuration_snapshot(
    configuration: object,
) -> object:
    """Return validated immutable components or the configuration failure sentinel."""

    try:
        if type(configuration) is not SessionKeyConfiguration:
            return _CONFIGURATION_FAILURE
        values = (
            object.__getattribute__(configuration, "_sentinel"),
            object.__getattribute__(configuration, "_lookup_current_epoch"),
            object.__getattribute__(configuration, "_lookup_current_key"),
            object.__getattribute__(configuration, "_lookup_previous_epoch"),
            object.__getattribute__(configuration, "_lookup_previous_key"),
            object.__getattribute__(configuration, "_binding_current_epoch"),
            object.__getattribute__(configuration, "_binding_current_key"),
            object.__getattribute__(configuration, "_binding_previous_epoch"),
            object.__getattribute__(configuration, "_binding_previous_key"),
        )
        if len(values) != 9 or values[0] is not _CONFIGURATION_SENTINEL:
            return _CONFIGURATION_FAILURE
        components = values[1:]
        if not _configuration_components_are_valid(*components):
            return _CONFIGURATION_FAILURE
        return components
    except Exception:
        return _CONFIGURATION_FAILURE


def _validated_cookie_header(raw_headers: object) -> str | None:
    if type(raw_headers) is not tuple or len(raw_headers) > _MAX_HEADER_PAIRS:
        return None

    total_bytes = 0
    for pair in raw_headers:
        if type(pair) is not tuple or len(pair) != 2:
            return None
        name, value = pair
        if type(name) is not str or type(value) is not str:
            return None
        if (
            len(name) < 1
            or len(name) > _MAX_HEADER_NAME_CHARACTERS
            or not name.isascii()
            or any(character not in _TOKEN_CHARACTERS for character in name)
            or len(value) > _MAX_HEADER_VALUE_BYTES
            or any(ord(character) <= 31 or ord(character) == 127 for character in value)
        ):
            return None
        try:
            value_bytes = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return None
        if len(value_bytes) > _MAX_HEADER_VALUE_BYTES:
            return None
        total_bytes += len(name) + len(value_bytes)
        if total_bytes > _MAX_TOTAL_HEADER_BYTES:
            return None

    cookie_header: str | None = None
    cookie_count = 0
    for name, value in raw_headers:
        if name.lower() == "cookie":
            cookie_count += 1
            cookie_header = value
    return cookie_header if cookie_count == 1 else None


def _is_cookie_octet(character: str) -> bool:
    codepoint = ord(character)
    return (
        codepoint == 0x21
        or 0x23 <= codepoint <= 0x2B
        or 0x2D <= codepoint <= 0x3A
        or 0x3C <= codepoint <= 0x5B
        or 0x5D <= codepoint <= 0x7E
    )


def _production_cookie_value(cookie_header: object) -> str | None:
    if (
        type(cookie_header) is not str
        or not cookie_header.isascii()
        or len(cookie_header) > _MAX_COOKIE_HEADER_BYTES
        or "," in cookie_header
        or "\t" in cookie_header
        or '"' in cookie_header
        or "\\" in cookie_header
    ):
        return None
    raw_segments = cookie_header.split(";")
    if not raw_segments or any(not segment for segment in raw_segments):
        return None

    names: set[str] = set()
    production_value: str | None = None
    production_count = 0
    for index, raw_segment in enumerate(raw_segments):
        segment = raw_segment
        if index:
            if segment.startswith(" "):
                segment = segment[1:]
                if segment.startswith(" "):
                    return None
            if not segment:
                return None
        if "=" not in segment:
            return None
        name, value = segment.split("=", 1)
        if (
            not name
            or any(character not in _TOKEN_CHARACTERS for character in name)
            or any(not _is_cookie_octet(character) for character in value)
            or name in names
        ):
            return None
        names.add(name)
        if name == _SESSION_COOKIE_NAME:
            production_count += 1
            production_value = value
    return production_value if production_count == 1 else None


def _credential_components(
    cookie_value: object,
) -> tuple[int, int, int, bytes] | None:
    if (
        type(cookie_value) is not str
        or not cookie_value.isascii()
        or len(cookie_value) > _MAX_CREDENTIAL_BYTES
    ):
        return None
    components = cookie_value.split(".")
    if len(components) != 5 or components[0] != _SESSION_VERSION:
        return None
    lookup_epoch = _parse_epoch(components[1])
    binding_epoch = _parse_epoch(components[2])
    credential_epoch = _parse_epoch(components[3])
    secret = _decode_canonical_32_bytes(components[4])
    if (
        lookup_epoch is None
        or binding_epoch is None
        or credential_epoch is None
        or secret is None
    ):
        return None
    return lookup_epoch, binding_epoch, credential_epoch, secret


def _select_key(
    requested_epoch: int,
    current_epoch: int,
    current_key: bytes,
    previous_epoch: int | None,
    previous_key: bytes | None,
) -> bytes | None:
    if requested_epoch == current_epoch:
        return current_key
    if previous_epoch is not None and requested_epoch == previous_epoch:
        return previous_key
    return None


def _frame(fields: tuple[bytes, ...]) -> bytes | None:
    framed = bytearray()
    for field in fields:
        if type(field) is not bytes:
            return None
        length = len(field)
        if length > _MAX_UINT32:
            return None
        framed.extend(length.to_bytes(4, "big", signed=False))
        framed.extend(field)
    return bytes(framed)


def _new_derived_credential(
    lookup_key_epoch: int,
    binding_key_epoch: int,
    credential_epoch: int,
    credential_lookup_digest: str,
    credential_binding_digest: str,
) -> DerivedSessionCredential:
    if (
        type(lookup_key_epoch) is not int
        or type(binding_key_epoch) is not int
        or type(credential_epoch) is not int
        or not (1 <= lookup_key_epoch <= _MAX_EPOCH)
        or not (1 <= binding_key_epoch <= _MAX_EPOCH)
        or not (1 <= credential_epoch <= _MAX_EPOCH)
        or _decode_canonical_32_bytes(credential_lookup_digest) is None
        or _decode_canonical_32_bytes(credential_binding_digest) is None
    ):
        raise TypeError(_DERIVED_CONSTRUCTION_ERROR)
    credential = object.__new__(DerivedSessionCredential)
    for name, value in (
        ("_sentinel", _DERIVED_CREDENTIAL_SENTINEL),
        ("_lookup_key_epoch", lookup_key_epoch),
        ("_binding_key_epoch", binding_key_epoch),
        ("_credential_epoch", credential_epoch),
        ("_credential_lookup_digest", credential_lookup_digest),
        ("_credential_binding_digest", credential_binding_digest),
    ):
        object.__setattr__(credential, name, value)
    return credential


def derive_request_session_credential(
    raw_headers: tuple[tuple[str, str], ...],
    configuration: SessionKeyConfiguration,
) -> DerivedSessionCredential | None:
    """Return only opaque digests derived from one canonical production cookie."""

    configuration_snapshot = _configuration_snapshot(configuration)
    if configuration_snapshot is _CONFIGURATION_FAILURE:
        del raw_headers
        del configuration
        del configuration_snapshot
        _raise_configuration_error()
    (
        lookup_current_epoch,
        lookup_current_key,
        lookup_previous_epoch,
        lookup_previous_key,
        binding_current_epoch,
        binding_current_key,
        binding_previous_epoch,
        binding_previous_key,
    ) = configuration_snapshot  # type: ignore[misc]

    cookie_header = _validated_cookie_header(raw_headers)
    if cookie_header is None:
        return None
    cookie_value = _production_cookie_value(cookie_header)
    if cookie_value is None:
        return None
    credential_components = _credential_components(cookie_value)
    if credential_components is None:
        return None
    (
        lookup_key_epoch,
        binding_key_epoch,
        credential_epoch,
        secret,
    ) = credential_components

    lookup_key = _select_key(
        lookup_key_epoch,
        lookup_current_epoch,
        lookup_current_key,
        lookup_previous_epoch,
        lookup_previous_key,
    )
    binding_key = _select_key(
        binding_key_epoch,
        binding_current_epoch,
        binding_current_key,
        binding_previous_epoch,
        binding_previous_key,
    )
    if lookup_key is None or binding_key is None:
        return None

    framed = _frame(
        (
            b"v1",
            str(lookup_key_epoch).encode("ascii"),
            str(binding_key_epoch).encode("ascii"),
            str(credential_epoch).encode("ascii"),
            secret,
        )
    )
    if framed is None:
        return None
    lookup_digest = _encode_base64url(
        _hmac.new(
            lookup_key,
            _LOOKUP_DOMAIN + framed,
            digestmod=_hashlib.sha256,
        ).digest()
    )
    binding_digest = _encode_base64url(
        _hmac.new(
            binding_key,
            _BINDING_DOMAIN + framed,
            digestmod=_hashlib.sha256,
        ).digest()
    )
    return _new_derived_credential(
        lookup_key_epoch,
        binding_key_epoch,
        credential_epoch,
        lookup_digest,
        binding_digest,
    )
