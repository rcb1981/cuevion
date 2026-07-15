from __future__ import annotations

if __name__ != "api.collaboration.owner_request_security":
    raise ImportError(
        "Collaboration helpers must be imported as "
        "api.collaboration.owner_request_security"
    )

import base64 as _base64
import hashlib as _hashlib
import hmac as _hmac
import json as _json
import re as _re
import secrets as _secrets
import unicodedata as _unicodedata
from dataclasses import FrozenInstanceError as _FrozenInstanceError
from dataclasses import dataclass as _dataclass
from urllib.parse import urlsplit as _urlsplit

from .http_boundary import BoundaryError as _BoundaryError
from .http_boundary import decode_strict_utf8 as _decode_strict_utf8
from .http_boundary import get_security_header as _get_security_header
from .http_boundary import parse_json_object as _parse_json_object


_PRODUCTION_ORIGIN = "https://app.cuevion.com"
_MAX_SAFE_INTEGER = (2**53) - 1
_CSRF_LIFETIME_SECONDS = 15 * 60
_CSRF_CLOCK_SKEW_SECONDS = 30
_CSRF_AUDIENCE = "cuevion-collaboration-v2-owner"
_CSRF_PURPOSE = "owner_csrf"
_CSRF_PREFIX = "oc1"

_ISSUER_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SUBJECT_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_SESSION_ID_RE = _re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_BASE64URL_RE = _re.compile(r"^[A-Za-z0-9_-]+$")
_DIGEST_RE = _re.compile(r"^[A-Za-z0-9_-]{43}$")
_ALLOWLIST_ENTRY_RE = _re.compile(r"^v1_[A-Za-z0-9_-]{43}$")
_MAILBOX_ID_RE = _re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_EMAIL_RE = _re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_CONFIG_ORIGIN = "CUEVION_APP_ORIGIN"
_CONFIG_CSRF_KEY = "CUEVION_COLLAB_V2_OWNER_CSRF_KEY"
_CONFIG_CSRF_PREVIOUS_KEY = "CUEVION_COLLAB_V2_OWNER_CSRF_KEY_PREVIOUS"
_CONFIG_ALLOWLIST_KEY = "CUEVION_COLLAB_V2_ALLOWLIST_HMAC_KEY"
_CONFIG_OWNER_ALLOWLIST = "CUEVION_COLLAB_V2_OWNER_ALLOWLIST"
_CONFIG_MAILBOX_ALLOWLIST = "CUEVION_COLLAB_V2_MAILBOX_ALLOWLIST"
_CONFIGURATION_KEYS = frozenset(
    {
        _CONFIG_ORIGIN,
        _CONFIG_CSRF_KEY,
        _CONFIG_CSRF_PREVIOUS_KEY,
        _CONFIG_ALLOWLIST_KEY,
        _CONFIG_OWNER_ALLOWLIST,
        _CONFIG_MAILBOX_ALLOWLIST,
    }
)
_REQUIRED_CONFIGURATION_KEYS = _CONFIGURATION_KEYS - {_CONFIG_CSRF_PREVIOUS_KEY}

_SIGNING_SUBKEY_LABEL = b"cuevion/collaboration-v2/owner-csrf/signing/v1"
_BINDING_SUBKEY_LABEL = b"cuevion/collaboration-v2/owner-csrf/session-binding/v1"
_BINDING_INPUT_DOMAIN = b"cuevion/collaboration-v2/owner-csrf/binding-input/v1\x00"
_OWNER_ALLOWLIST_DOMAIN = b"cuevion/collaboration-v2/owner-allowlist/v1\x00"
_MAILBOX_ALLOWLIST_DOMAIN = b"cuevion/collaboration-v2/mailbox-allowlist/v1\x00"

_PAYLOAD_FIELDS = frozenset({"aud", "exp", "iat", "n", "o", "p", "s", "v"})
_INTERNAL_REASONS = frozenset(
    {
        "invalid_configuration",
        "authentication_required",
        "authentication_unavailable",
        "forbidden_origin",
        "invalid_csrf",
        "rollout_unavailable",
        "internal_error",
    }
)
_AUTHENTICATION_RESOLVER_REASONS = frozenset(
    {"authentication_required", "authentication_unavailable"}
)
_PUBLIC_FAILURES = {
    "authentication_required": (401, "unauthorized"),
    "authentication_unavailable": (503, "service_unavailable"),
    "forbidden_origin": (403, "forbidden"),
    "invalid_csrf": (403, "forbidden"),
    "rollout_unavailable": (404, "not_found"),
    "invalid_configuration": (503, "service_unavailable"),
    "internal_error": (500, "internal_error"),
}
_INTERNAL_ERROR_PAIR = (500, "internal_error")
_OWNER_CONTEXT_SENTINEL = object()
_CONFIGURATION_SENTINEL = object()


class OwnerSecurityError(Exception):
    """A fixed internal failure with no request or provider detail."""

    __slots__ = ("reason",)

    def __new__(cls, *_args: object, **_kwargs: object) -> OwnerSecurityError:
        return Exception.__new__(cls)

    def __init__(self, reason: object) -> None:
        self.reason = (
            reason
            if type(reason) is str and reason in _INTERNAL_REASONS
            else "internal_error"
        )
        Exception.__init__(self)


def _safe_owner_security_reason(value: object) -> str | None:
    """Extract one exact approved reason without invoking user behavior."""

    if type(value) is not OwnerSecurityError:
        return None
    candidate: object | None = None
    try:
        candidate = object.__getattribute__(value, "reason")
    except Exception:
        pass
    if type(candidate) is not str or candidate not in _INTERNAL_REASONS:
        return None
    return candidate


def _raise_security(reason: str) -> None:
    raise OwnerSecurityError(reason)


def _valid_utf8_display_name(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    encoded: bytes | None = None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        pass
    return (
        encoded is not None
        and len(encoded) <= 256
        and not any(
            _unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in value
        )
    )


def _valid_canonical_email(value: object) -> bool:
    if (
        type(value) is not str
        or not value.isascii()
        or value != value.lower()
        or len(value) > 320
        or _EMAIL_RE.fullmatch(value) is None
    ):
        return False
    local_part, domain = value.rsplit("@", 1)
    return len(local_part) <= 64 and len(domain) <= 253


def _decode_canonical_base64url(value: object) -> bytes | None:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or _BASE64URL_RE.fullmatch(value) is None
        or len(value) % 4 == 1
    ):
        return None
    decoded: bytes | None = None
    try:
        decoded = _base64.b64decode(
            value.encode("ascii") + (b"=" * ((-len(value)) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except Exception:
        pass
    if decoded is None or _encode_base64url(decoded) != value:
        return None
    return decoded


def _encode_base64url(value: bytes) -> str:
    return _base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and _DIGEST_RE.fullmatch(value) is not None
        and (decoded := _decode_canonical_base64url(value)) is not None
        and len(decoded) == 32
    )


def _valid_verified_authentication_fields(value: object) -> bool:
    if type(value) is not VerifiedOwnerAuthentication:
        return False
    try:
        issuer = object.__getattribute__(value, "issuer")
        authentication_version = object.__getattribute__(
            value, "authentication_version"
        )
        subject = object.__getattribute__(value, "subject")
        owner_email = object.__getattribute__(value, "owner_email")
        display_name = object.__getattribute__(value, "display_name")
        session_id = object.__getattribute__(value, "session_id")
        credential_digest = object.__getattribute__(value, "credential_digest")
        issued_at = object.__getattribute__(value, "issued_at")
        expires_at = object.__getattribute__(value, "expires_at")
    except Exception:
        return False
    return (
        type(issuer) is str
        and issuer.isascii()
        and _ISSUER_RE.fullmatch(issuer) is not None
        and type(authentication_version) is int
        and 1 <= authentication_version <= _MAX_SAFE_INTEGER
        and type(subject) is str
        and subject.isascii()
        and _SUBJECT_RE.fullmatch(subject) is not None
        and _valid_canonical_email(owner_email)
        and _valid_utf8_display_name(display_name)
        and type(session_id) is str
        and session_id.isascii()
        and _SESSION_ID_RE.fullmatch(session_id) is not None
        and _valid_digest(credential_digest)
        and type(issued_at) is int
        and type(expires_at) is int
        and 0 <= issued_at < expires_at <= _MAX_SAFE_INTEGER
    )


@_dataclass(frozen=True, slots=True, repr=False)
class VerifiedOwnerAuthentication:
    """Claims minted only by a future verified authentication provider.

    No raw credential, cookie, bearer token, or provider secret belongs here.
    """

    issuer: str
    authentication_version: int
    subject: str
    owner_email: str
    display_name: str
    session_id: str
    credential_digest: str
    issued_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if not _valid_verified_authentication_fields(self):
            raise ValueError("invalid verified owner authentication")


class OwnerRequestContext:
    """Opaque immutable owner context minted only by the trusted resolver."""

    __slots__ = (
        "_sentinel",
        "issuer",
        "authentication_version",
        "subject",
        "owner_email",
        "workspace_id",
        "display_name",
        "session_id",
        "credential_digest",
        "issued_at",
        "expires_at",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> OwnerRequestContext:
        raise TypeError("OwnerRequestContext is resolver-minted")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise _FrozenInstanceError("OwnerRequestContext is immutable")

    def __delattr__(self, _name: str) -> None:
        raise _FrozenInstanceError("OwnerRequestContext is immutable")

    def __repr__(self) -> str:
        return "<OwnerRequestContext>"

    __str__ = __repr__
    __hash__ = object.__hash__

    def __copy__(self) -> OwnerRequestContext:
        return self

    def __deepcopy__(self, _memo: object) -> OwnerRequestContext:
        return self

    def __reduce__(self) -> object:
        raise TypeError("OwnerRequestContext is not serializable")

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError("OwnerRequestContext is not serializable")

    def __getstate__(self) -> object:
        raise TypeError("OwnerRequestContext is not serializable")

    def __setstate__(self, _state: object) -> None:
        raise TypeError("OwnerRequestContext is not serializable")


class OwnerSecurityConfiguration:
    """Opaque immutable security configuration parsed from trusted process data."""

    __slots__ = (
        "_sentinel",
        "app_origin",
        "owner_allowlist",
        "mailbox_allowlist",
        "_csrf_key",
        "_csrf_previous_key",
        "_allowlist_hmac_key",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> OwnerSecurityConfiguration:
        raise TypeError("OwnerSecurityConfiguration is parser-minted")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise _FrozenInstanceError("OwnerSecurityConfiguration is immutable")

    def __delattr__(self, _name: str) -> None:
        raise _FrozenInstanceError("OwnerSecurityConfiguration is immutable")

    def __repr__(self) -> str:
        return "<OwnerSecurityConfiguration>"

    __str__ = __repr__
    __hash__ = object.__hash__

    def __copy__(self) -> OwnerSecurityConfiguration:
        return self

    def __deepcopy__(self, _memo: object) -> OwnerSecurityConfiguration:
        return self

    def __reduce__(self) -> object:
        raise TypeError("OwnerSecurityConfiguration is not serializable")

    def __reduce_ex__(self, _protocol: object) -> object:
        raise TypeError("OwnerSecurityConfiguration is not serializable")

    def __getstate__(self) -> object:
        raise TypeError("OwnerSecurityConfiguration is not serializable")

    def __setstate__(self, _state: object) -> None:
        raise TypeError("OwnerSecurityConfiguration is not serializable")


def _new_owner_context(claims: VerifiedOwnerAuthentication) -> OwnerRequestContext:
    if not _valid_verified_authentication_fields(claims):
        _raise_security("authentication_required")
    context = object.__new__(OwnerRequestContext)
    for name, value in (
        ("_sentinel", _OWNER_CONTEXT_SENTINEL),
        ("issuer", claims.issuer),
        ("authentication_version", claims.authentication_version),
        ("subject", claims.subject),
        ("owner_email", claims.owner_email),
        ("workspace_id", claims.owner_email),
        ("display_name", claims.display_name),
        ("session_id", claims.session_id),
        ("credential_digest", claims.credential_digest),
        ("issued_at", claims.issued_at),
        ("expires_at", claims.expires_at),
    ):
        object.__setattr__(context, name, value)
    return context


def resolve_owner_request_context(
    raw_headers: object,
    *,
    authentication_resolver: object,
    now: object,
) -> OwnerRequestContext:
    """Resolve one future-provider authentication result without retry/fallback.

    No route may call this until a real authentication provider can prove the
    owner identity and mint all fields of ``VerifiedOwnerAuthentication``. A
    trusted resolver signals a missing/invalid session or provider outage only
    with the corresponding fixed ``OwnerSecurityError`` reason; every other
    ordinary exception is unexpected and becomes ``internal_error``.
    """

    if not callable(authentication_resolver) or type(now) is not int or now < 0:
        _raise_security("invalid_configuration")

    resolver_failure: str | None = None
    claims: object | None = None
    try:
        claims = authentication_resolver(raw_headers)  # type: ignore[operator]
    except OwnerSecurityError as error:
        extracted_reason = _safe_owner_security_reason(error)
        resolver_failure = (
            extracted_reason
            if extracted_reason in _AUTHENTICATION_RESOLVER_REASONS
            else "internal_error"
        )
    except Exception:
        resolver_failure = "internal_error"
    if resolver_failure is not None:
        _raise_security(resolver_failure)

    try:
        valid_claims = (
            type(claims) is VerifiedOwnerAuthentication
            and _valid_verified_authentication_fields(claims)
            and claims.issued_at <= now < claims.expires_at
        )
    except Exception:
        valid_claims = False
    if not valid_claims:
        _raise_security("authentication_required")
    return _new_owner_context(claims)  # type: ignore[arg-type]


def parse_trusted_owner_origin(value: object) -> str:
    """Parse the one reviewed production Origin from trusted configuration."""

    if (
        type(value) is not str
        or not value.isascii()
        or value != _PRODUCTION_ORIGIN
        or any(character.isspace() for character in value)
        or "\x00" in value
        or "," in value
    ):
        _raise_security("invalid_configuration")
    try:
        parsed = _urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.netloc == "app.cuevion.com"
            and parsed.hostname == "app.cuevion.com"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path == ""
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except Exception:
        valid = False
    if not valid:
        _raise_security("invalid_configuration")
    return value


def _parse_secret_key(value: object) -> bytes:
    if type(value) is not str:
        _raise_security("invalid_configuration")
    decoded = _decode_canonical_base64url(value)
    if decoded is None or len(decoded) < 32:
        _raise_security("invalid_configuration")
    return decoded


def _parse_allowlist(value: object) -> tuple[str, ...]:
    if type(value) is not str or not value:
        _raise_security("invalid_configuration")
    entries = value.split(",")
    if (
        any(not entry or any(character.isspace() for character in entry) for entry in entries)
        or len(set(entries)) != len(entries)
        or any(
            _ALLOWLIST_ENTRY_RE.fullmatch(entry) is None
            or (decoded := _decode_canonical_base64url(entry[3:])) is None
            or len(decoded) != 32
            for entry in entries
        )
    ):
        _raise_security("invalid_configuration")
    return tuple(entries)


def _new_configuration(
    origin: str,
    csrf_key: bytes,
    csrf_previous_key: bytes | None,
    allowlist_hmac_key: bytes,
    owner_allowlist: tuple[str, ...],
    mailbox_allowlist: tuple[str, ...],
) -> OwnerSecurityConfiguration:
    configuration = object.__new__(OwnerSecurityConfiguration)
    copied_origin = origin.encode("ascii").decode("ascii")
    copied_csrf_key = bytes(bytearray(csrf_key))
    copied_previous_key = (
        None
        if csrf_previous_key is None
        else bytes(bytearray(csrf_previous_key))
    )
    copied_allowlist_key = bytes(bytearray(allowlist_hmac_key))
    copied_owner_allowlist = tuple(
        entry.encode("ascii").decode("ascii") for entry in owner_allowlist
    )
    copied_mailbox_allowlist = tuple(
        entry.encode("ascii").decode("ascii") for entry in mailbox_allowlist
    )
    for name, value in (
        ("_sentinel", _CONFIGURATION_SENTINEL),
        ("app_origin", copied_origin),
        ("owner_allowlist", copied_owner_allowlist),
        ("mailbox_allowlist", copied_mailbox_allowlist),
        ("_csrf_key", copied_csrf_key),
        ("_csrf_previous_key", copied_previous_key),
        ("_allowlist_hmac_key", copied_allowlist_key),
    ):
        object.__setattr__(configuration, name, value)
    return configuration


def parse_owner_security_configuration(
    trusted_configuration: object,
) -> OwnerSecurityConfiguration:
    """Parse explicitly supplied trusted process configuration; never the environment."""

    if type(trusted_configuration) is not dict:
        _raise_security("invalid_configuration")

    keys = tuple(dict.__iter__(trusted_configuration))
    if any(type(key) is not str for key in keys):
        _raise_security("invalid_configuration")
    key_set = frozenset(keys)
    if (
        not _REQUIRED_CONFIGURATION_KEYS.issubset(key_set)
        or not key_set.issubset(_CONFIGURATION_KEYS)
    ):
        _raise_security("invalid_configuration")

    snapshot = dict.copy(trusted_configuration)
    origin_value = dict.__getitem__(snapshot, _CONFIG_ORIGIN)
    csrf_value = dict.__getitem__(snapshot, _CONFIG_CSRF_KEY)
    has_previous_key = _CONFIG_CSRF_PREVIOUS_KEY in key_set
    previous_value = (
        dict.__getitem__(snapshot, _CONFIG_CSRF_PREVIOUS_KEY)
        if has_previous_key
        else None
    )
    allowlist_key_value = dict.__getitem__(snapshot, _CONFIG_ALLOWLIST_KEY)
    owner_allowlist_value = dict.__getitem__(snapshot, _CONFIG_OWNER_ALLOWLIST)
    mailbox_allowlist_value = dict.__getitem__(snapshot, _CONFIG_MAILBOX_ALLOWLIST)

    invalid_configuration = False
    origin = ""
    csrf_key = b""
    previous_key: bytes | None = None
    allowlist_key = b""
    owner_allowlist: tuple[str, ...] = ()
    mailbox_allowlist: tuple[str, ...] = ()
    try:
        origin = parse_trusted_owner_origin(origin_value)
        csrf_key = _parse_secret_key(csrf_value)
        previous_key = (
            _parse_secret_key(previous_value) if has_previous_key else None
        )
        allowlist_key = _parse_secret_key(allowlist_key_value)
        owner_allowlist = _parse_allowlist(owner_allowlist_value)
        mailbox_allowlist = _parse_allowlist(mailbox_allowlist_value)
        if (
            (previous_key is not None and _hmac.compare_digest(csrf_key, previous_key))
            or _hmac.compare_digest(csrf_key, allowlist_key)
            or (
                previous_key is not None
                and _hmac.compare_digest(previous_key, allowlist_key)
            )
        ):
            _raise_security("invalid_configuration")
    except Exception:
        invalid_configuration = True
    if invalid_configuration:
        _raise_security("invalid_configuration")

    creation_failed = False
    configuration: OwnerSecurityConfiguration | None = None
    try:
        configuration = _new_configuration(
            origin,
            csrf_key,
            previous_key,
            allowlist_key,
            owner_allowlist,
            mailbox_allowlist,
        )
    except Exception:
        creation_failed = True
    if creation_failed:
        _raise_security("invalid_configuration")
    return configuration  # type: ignore[return-value]


def _require_configuration(value: object) -> OwnerSecurityConfiguration:
    if type(value) is not OwnerSecurityConfiguration:
        _raise_security("invalid_configuration")
    missing_field = False
    try:
        sentinel = object.__getattribute__(value, "_sentinel")
        app_origin = object.__getattribute__(value, "app_origin")
        owner_allowlist = object.__getattribute__(value, "owner_allowlist")
        mailbox_allowlist = object.__getattribute__(value, "mailbox_allowlist")
        csrf_key = object.__getattribute__(value, "_csrf_key")
        previous_key = object.__getattribute__(value, "_csrf_previous_key")
        allowlist_key = object.__getattribute__(value, "_allowlist_hmac_key")
    except Exception:
        missing_field = True
    if missing_field:
        _raise_security("invalid_configuration")
    if (
        sentinel is not _CONFIGURATION_SENTINEL
        or type(app_origin) is not str
        or app_origin != _PRODUCTION_ORIGIN
        or type(owner_allowlist) is not tuple
        or not owner_allowlist
        or any(type(entry) is not str for entry in owner_allowlist)
        or type(mailbox_allowlist) is not tuple
        or not mailbox_allowlist
        or any(type(entry) is not str for entry in mailbox_allowlist)
        or type(csrf_key) is not bytes
        or len(csrf_key) < 32
        or (
            previous_key is not None
            and (type(previous_key) is not bytes or len(previous_key) < 32)
        )
        or type(allowlist_key) is not bytes
        or len(allowlist_key) < 32
    ):
        _raise_security("invalid_configuration")
    return value


def _is_owner_context(value: object) -> bool:
    if type(value) is not OwnerRequestContext:
        return False
    try:
        sentinel = object.__getattribute__(value, "_sentinel")
        issuer = object.__getattribute__(value, "issuer")
        authentication_version = object.__getattribute__(
            value, "authentication_version"
        )
        subject = object.__getattribute__(value, "subject")
        owner_email = object.__getattribute__(value, "owner_email")
        workspace_id = object.__getattribute__(value, "workspace_id")
        display_name = object.__getattribute__(value, "display_name")
        session_id = object.__getattribute__(value, "session_id")
        credential_digest = object.__getattribute__(value, "credential_digest")
        issued_at = object.__getattribute__(value, "issued_at")
        expires_at = object.__getattribute__(value, "expires_at")
    except Exception:
        return False
    return (
        sentinel is _OWNER_CONTEXT_SENTINEL
        and type(issuer) is str
        and issuer.isascii()
        and _ISSUER_RE.fullmatch(issuer) is not None
        and type(authentication_version) is int
        and 1 <= authentication_version <= _MAX_SAFE_INTEGER
        and type(subject) is str
        and subject.isascii()
        and _SUBJECT_RE.fullmatch(subject) is not None
        and _valid_canonical_email(owner_email)
        and type(workspace_id) is str
        and workspace_id == owner_email
        and _valid_utf8_display_name(display_name)
        and type(session_id) is str
        and session_id.isascii()
        and _SESSION_ID_RE.fullmatch(session_id) is not None
        and _valid_digest(credential_digest)
        and type(issued_at) is int
        and type(expires_at) is int
        and 0 <= issued_at < expires_at <= _MAX_SAFE_INTEGER
    )


def validate_owner_mutation_origin(
    raw_headers: object,
    configuration: object,
) -> str:
    """Require one byte-exact configured Origin from duplicate-preserving headers."""

    validated_configuration = _require_configuration(configuration)
    invalid_origin = False
    origin: str | None = None
    try:
        origin = _get_security_header(raw_headers, "origin", required=True)
    except _BoundaryError:
        invalid_origin = True
    except Exception:
        invalid_origin = True
    if invalid_origin:
        _raise_security("forbidden_origin")
    if type(origin) is not str or origin != validated_configuration.app_origin:
        _raise_security("forbidden_origin")
    return validated_configuration.app_origin


def _strict_csrf_token(value: object) -> tuple[str, bytes, bytes]:
    if (
        type(value) is not str
        or not value.isascii()
        or len(value) > 512
        or any(character.isspace() for character in value)
        or "," in value
    ):
        _raise_security("invalid_csrf")
    segments = value.split(".")
    if len(segments) != 3 or any(not segment for segment in segments):
        _raise_security("invalid_csrf")
    prefix, payload_segment, signature_segment = segments
    if prefix != _CSRF_PREFIX or len(signature_segment) != 43:
        _raise_security("invalid_csrf")
    payload_bytes = _decode_canonical_base64url(payload_segment)
    signature_bytes = _decode_canonical_base64url(signature_segment)
    if payload_bytes is None or signature_bytes is None or len(signature_bytes) != 32:
        _raise_security("invalid_csrf")
    return payload_segment, payload_bytes, signature_bytes


def parse_owner_csrf_header(raw_headers: object) -> str:
    """Extract one exact X-Cuevion-CSRF header and validate its outer syntax."""

    invalid_csrf = False
    token: object | None = None
    try:
        token = _get_security_header(raw_headers, "x-cuevion-csrf", required=True)
        _strict_csrf_token(token)
    except Exception:
        invalid_csrf = True
    if invalid_csrf:
        _raise_security("invalid_csrf")
    return token  # type: ignore[return-value]


def _derive_subkey(root_key: bytes, label: bytes) -> bytes:
    return _hmac.new(root_key, label, _hashlib.sha256).digest()


def _canonical_json(value: object) -> str:
    return _json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _session_binding_input(context: OwnerRequestContext) -> bytes:
    values = (
        context.issuer,
        str(context.authentication_version),
        context.subject,
        context.owner_email,
        context.workspace_id,
        context.session_id,
        context.credential_digest,
        str(context.issued_at),
        str(context.expires_at),
    )
    framed = bytearray(_BINDING_INPUT_DOMAIN)
    for value in values:
        encoded = value.encode("ascii")
        framed.extend(len(encoded).to_bytes(4, "big"))
        framed.extend(encoded)
    return bytes(framed)


def _session_binding(root_key: bytes, context: OwnerRequestContext) -> bytes:
    binding_key = _derive_subkey(root_key, _BINDING_SUBKEY_LABEL)
    return _hmac.new(
        binding_key,
        _session_binding_input(context),
        _hashlib.sha256,
    ).digest()


def _origin_digest(origin: str) -> bytes:
    return _hashlib.sha256(origin.encode("ascii")).digest()


def _token_signature(root_key: bytes, payload_segment: str) -> bytes:
    signing_key = _derive_subkey(root_key, _SIGNING_SUBKEY_LABEL)
    signed = (_CSRF_PREFIX + "." + payload_segment).encode("ascii")
    return _hmac.new(signing_key, signed, _hashlib.sha256).digest()


def issue_owner_csrf_token(
    context: object,
    configuration: object,
    *,
    now: object,
) -> tuple[str, int]:
    """Issue one current-key stateless token for an already-resolved owner session."""

    validated_configuration = _require_configuration(configuration)
    if (
        not _is_owner_context(context)
        or type(now) is not int
        or now < context.issued_at
        or now >= context.expires_at
    ):
        _raise_security("authentication_required")
    expires_at = min(now + _CSRF_LIFETIME_SECONDS, context.expires_at)
    if expires_at <= now:
        _raise_security("authentication_required")
    nonce = _secrets.token_bytes(16)
    if type(nonce) is not bytes or len(nonce) != 16:
        _raise_security("internal_error")
    payload = {
        "aud": _CSRF_AUDIENCE,
        "exp": expires_at,
        "iat": now,
        "n": _encode_base64url(nonce),
        "o": _encode_base64url(_origin_digest(validated_configuration.app_origin)),
        "p": _CSRF_PURPOSE,
        "s": _encode_base64url(
            _session_binding(validated_configuration._csrf_key, context)
        ),
        "v": 1,
    }
    payload_segment = _encode_base64url(_canonical_json(payload).encode("utf-8"))
    signature = _token_signature(validated_configuration._csrf_key, payload_segment)
    token = ".".join((_CSRF_PREFIX, payload_segment, _encode_base64url(signature)))
    return token, expires_at


def _parse_token_payload(payload_bytes: bytes) -> dict[str, object]:
    text = _decode_strict_utf8(payload_bytes)
    payload = _parse_json_object(
        text,
        allowed_fields=_PAYLOAD_FIELDS,
        required_fields=_PAYLOAD_FIELDS,
        reject_numbers=False,
    )
    if _canonical_json(payload) != text:
        _raise_security("invalid_csrf")
    return payload


def verify_owner_csrf_token(
    token: object,
    context: object,
    configuration: object,
    *,
    now: object,
) -> bool:
    """Verify syntax, payload, both active key paths, and exact session binding."""

    validated_configuration = _require_configuration(configuration)
    invalid_csrf = False
    try:
        if not _is_owner_context(context) or type(now) is not int:
            _raise_security("invalid_csrf")
        payload_segment, payload_bytes, supplied_signature = _strict_csrf_token(token)
        payload = _parse_token_payload(payload_bytes)

        aud = payload["aud"]
        expires_at = payload["exp"]
        issued_at = payload["iat"]
        nonce = payload["n"]
        origin_digest = payload["o"]
        purpose = payload["p"]
        supplied_binding = payload["s"]
        version = payload["v"]
        nonce_bytes = _decode_canonical_base64url(nonce)
        binding_bytes = _decode_canonical_base64url(supplied_binding)
        origin_bytes = _decode_canonical_base64url(origin_digest)

        valid_payload = (
            type(aud) is str
            and aud == _CSRF_AUDIENCE
            and type(expires_at) is int
            and type(issued_at) is int
            and type(nonce) is str
            and nonce_bytes is not None
            and len(nonce_bytes) == 16
            and type(origin_digest) is str
            and origin_bytes is not None
            and len(origin_bytes) == 32
            and type(purpose) is str
            and purpose == _CSRF_PURPOSE
            and type(supplied_binding) is str
            and binding_bytes is not None
            and len(binding_bytes) == 32
            and type(version) is int
            and version == 1
            and context.issued_at <= issued_at <= now + _CSRF_CLOCK_SKEW_SECONDS
            and issued_at < expires_at
            and expires_at - issued_at <= _CSRF_LIFETIME_SECONDS
            and now < expires_at
            and expires_at <= context.expires_at
        )
        if not valid_payload:
            _raise_security("invalid_csrf")

        origin_matches = _hmac.compare_digest(
            _origin_digest(validated_configuration.app_origin),
            origin_bytes,
        )
        current_signature_matches = _hmac.compare_digest(
            _token_signature(validated_configuration._csrf_key, payload_segment),
            supplied_signature,
        )
        current_binding_matches = _hmac.compare_digest(
            _session_binding(validated_configuration._csrf_key, context),
            binding_bytes,
        )

        previous_signature_matches = False
        previous_binding_matches = False
        if validated_configuration._csrf_previous_key is not None:
            previous_signature_matches = _hmac.compare_digest(
                _token_signature(
                    validated_configuration._csrf_previous_key,
                    payload_segment,
                ),
                supplied_signature,
            )
            previous_binding_matches = _hmac.compare_digest(
                _session_binding(validated_configuration._csrf_previous_key, context),
                binding_bytes,
            )

        verified = origin_matches and (
            (current_signature_matches and current_binding_matches)
            or (previous_signature_matches and previous_binding_matches)
        )
        if not verified:
            _raise_security("invalid_csrf")
    except Exception:
        invalid_csrf = True
    if invalid_csrf:
        _raise_security("invalid_csrf")
    return True


def _framed_allowlist_input(domain: bytes, values: tuple[str, ...]) -> bytes:
    framed = bytearray(domain)
    for value in values:
        encoded = value.encode("ascii")
        framed.extend(len(encoded).to_bytes(4, "big"))
        framed.extend(encoded)
    return bytes(framed)


def _allowlist_entry(
    key: bytes,
    domain: bytes,
    values: tuple[str, ...],
) -> str:
    digest = _hmac.new(
        key,
        _framed_allowlist_input(domain, values),
        _hashlib.sha256,
    ).digest()
    return "v1_" + _encode_base64url(digest)


def _matches_all_entries(candidate: str, entries: tuple[str, ...]) -> bool:
    matched = False
    for entry in entries:
        matched |= _hmac.compare_digest(candidate, entry)
    return matched is True


def owner_is_allowlisted(context: object, configuration: object) -> bool:
    """Return one exact bool without revealing which rollout entry matched."""

    if not _is_owner_context(context):
        return False
    try:
        validated_configuration = _require_configuration(configuration)
    except OwnerSecurityError:
        return False
    candidate = _allowlist_entry(
        validated_configuration._allowlist_hmac_key,
        _OWNER_ALLOWLIST_DOMAIN,
        (context.issuer, str(context.authentication_version), context.subject),
    )
    return _matches_all_entries(candidate, validated_configuration.owner_allowlist)


def mailbox_is_allowlisted(
    context: object,
    mailbox_id: object,
    configuration: object,
) -> bool:
    """Return one exact bool for a canonical mailbox rollout digest."""

    if (
        not _is_owner_context(context)
        or type(mailbox_id) is not str
        or not mailbox_id.isascii()
        or _MAILBOX_ID_RE.fullmatch(mailbox_id) is None
    ):
        return False
    try:
        validated_configuration = _require_configuration(configuration)
    except OwnerSecurityError:
        return False
    candidate = _allowlist_entry(
        validated_configuration._allowlist_hmac_key,
        _MAILBOX_ALLOWLIST_DOMAIN,
        (
            context.issuer,
            str(context.authentication_version),
            context.subject,
            mailbox_id,
        ),
    )
    return _matches_all_entries(candidate, validated_configuration.mailbox_allowlist)


def normalize_owner_security_failure(reason: object) -> tuple[int, str]:
    """Map only exact fixed internal reasons to immutable public pairs."""

    if type(reason) is OwnerSecurityError:
        candidate = _safe_owner_security_reason(reason)
    elif type(reason) is str:
        candidate = reason
    else:
        return _INTERNAL_ERROR_PAIR
    if type(candidate) is not str:
        return _INTERNAL_ERROR_PAIR
    return _PUBLIC_FAILURES.get(candidate, _INTERNAL_ERROR_PAIR)


__all__ = (
    "VerifiedOwnerAuthentication",
    "OwnerRequestContext",
    "OwnerSecurityConfiguration",
    "OwnerSecurityError",
    "parse_owner_security_configuration",
    "parse_trusted_owner_origin",
    "validate_owner_mutation_origin",
    "parse_owner_csrf_header",
    "resolve_owner_request_context",
    "issue_owner_csrf_token",
    "verify_owner_csrf_token",
    "owner_is_allowlisted",
    "mailbox_is_allowlisted",
    "normalize_owner_security_failure",
)
