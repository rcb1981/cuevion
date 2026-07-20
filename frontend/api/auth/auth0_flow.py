"""Fail-closed Auth0 Authorization Code + PKCE protocol primitives.

This module owns protocol construction, the authenticated transaction cookie,
bounded Auth0 response parsing, and cryptographic ID-token verification.  It
does not create Cuevion accounts, select workspaces, store sessions, or expose
an HTTP route.  Network operations are behind an injectable transport.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


AUTH0_DOMAIN = "cuevion-dev.eu.auth0.com"
AUTH0_ISSUER = f"https://{AUTH0_DOMAIN}/"
AUTH0_AUTHORIZE_ENDPOINT = f"https://{AUTH0_DOMAIN}/authorize"
AUTH0_TOKEN_ENDPOINT = f"https://{AUTH0_DOMAIN}/oauth/token"
AUTH0_JWKS_ENDPOINT = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
AUTH0_LOGOUT_ENDPOINT = f"https://{AUTH0_DOMAIN}/v2/logout"
CALLBACK_URI = "https://app.cuevion.com/api/auth/callback"
LOGOUT_RETURN_TO = "https://app.cuevion.com/login"

AUTH_TRANSACTION_COOKIE_NAME = "__Host-cuevion_auth_tx"
SESSION_COOKIE_NAME = "__Host-cuevion_session"
AUTH_TRANSACTION_TTL_SECONDS = 10 * 60
SESSION_TTL_SECONDS = 8 * 60 * 60

_AUTHORIZATION_SCOPE = "openid profile email"
_AUTHORIZATION_CONNECTION = "email"
_AUTHORIZATION_PROMPT = "login"
_TRANSACTION_VERSION = 1
_TRANSACTION_COOKIE_VERSION = "v1"
_TRANSACTION_NONCE_BYTES = 12
_OPAQUE_VALUE_BYTES = 32
_MAX_COOKIE_VALUE_BYTES = 4_096
_MAX_TRANSACTION_PLAINTEXT_BYTES = 2_048
_MAX_AUTHORIZATION_URL_BYTES = 8_192
_MAX_CODE_BYTES = 2_048
_MAX_ID_TOKEN_BYTES = 32_768
_MAX_TOKEN_RESPONSE_BYTES = 256 * 1024
_MAX_JWKS_RESPONSE_BYTES = 256 * 1024
_MAX_JSON_DEPTH = 16
_MAX_JWKS_KEYS = 32
_MAX_JWK_MODULUS_BYTES = 1_024
_MIN_JWK_MODULUS_BITS = 2_048
_MAX_ID_TOKEN_AGE_SECONDS = 24 * 60 * 60
_MAX_CLOCK_SKEW_SECONDS = 60
_MAX_UNIX_TIMESTAMP = 253_402_300_799
_AUTH0_TIMEOUT_SECONDS = 5

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PKCE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_VISIBLE_ASCII_RE = re.compile(r"^[!-~]+$")
_EMAIL_RE = re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_TRANSACTION_KEY_DOMAIN = b"cuevion/auth0/transaction-cookie-key/v1\x00"
_TRANSACTION_AAD = (
    b"cuevion/auth0/transaction-cookie/v1\x00"
    b"__Host-cuevion_auth_tx\x00app.cuevion.com\x00/api/auth/callback"
)

_SAFE_ERROR_CODES = frozenset(
    {
        "internal_error",
        "invalid_configuration",
        "invalid_id_token",
        "invalid_jwks",
        "invalid_token_response",
        "invalid_transaction",
        "provider_unavailable",
    }
)


class Auth0FlowError(Exception):
    """One fixed, value-free Auth0 protocol failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe_code = code if code in _SAFE_ERROR_CODES else "internal_error"
        self.code = safe_code
        Exception.__init__(self, safe_code)

    def __repr__(self) -> str:
        return f"Auth0FlowError({self.code!r})"


def _fail(code: str) -> None:
    error = Auth0FlowError(code)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class Auth0Configuration:
    domain: str
    client_id: str
    client_secret: str
    session_secret: str

    def __repr__(self) -> str:
        return "Auth0Configuration(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AuthTransaction:
    state: str
    nonce: str
    code_verifier: str
    issued_at: int
    expires_at: int

    def __repr__(self) -> str:
        return "AuthTransaction(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AuthorizationRequest:
    authorization_url: str
    transaction_cookie: str
    transaction: AuthTransaction

    def __repr__(self) -> str:
        return "AuthorizationRequest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OutboundRequest:
    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes | None
    timeout_seconds: int
    max_response_bytes: int

    def __repr__(self) -> str:
        return f"OutboundRequest(method={self.method!r}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OutboundResponse:
    status: int
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __repr__(self) -> str:
        return f"OutboundResponse(status={self.status}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TokenResponse:
    id_token: str

    def __repr__(self) -> str:
        return "TokenResponse(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedIdentityEvidence:
    issuer: str
    subject: str
    email: str
    issued_at: int
    expires_at: int

    def __repr__(self) -> str:
        return "ValidatedIdentityEvidence(<redacted>)"


def _is_bounded_visible_ascii(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.isascii()
        and _VISIBLE_ASCII_RE.fullmatch(value) is not None
    )


def _validate_configuration(configuration: object) -> Auth0Configuration:
    if type(configuration) is not Auth0Configuration:
        _fail("invalid_configuration")
    try:
        valid = (
            configuration.domain == AUTH0_DOMAIN
            and _is_bounded_visible_ascii(configuration.client_id, 512)
            and _is_bounded_visible_ascii(configuration.client_secret, 4_096)
            and type(configuration.session_secret) is str
            and configuration.session_secret
            == configuration.session_secret.strip()
            and 32
            <= len(configuration.session_secret.encode("utf-8", errors="strict"))
            <= 4_096
            and all(
                ord(character) > 31 and ord(character) != 127
                for character in configuration.session_secret
            )
        )
    except Exception:
        valid = False
    if not valid:
        _fail("invalid_configuration")
    return configuration


def parse_auth0_configuration(values: Mapping[str, str]) -> Auth0Configuration:
    """Parse only the four reviewed Auth0 variables from a supplied mapping."""

    names = (
        "CUEVION_AUTH0_DOMAIN",
        "CUEVION_AUTH0_CLIENT_ID",
        "CUEVION_AUTH0_CLIENT_SECRET",
        "CUEVION_AUTH_SESSION_SECRET",
    )
    try:
        if not isinstance(values, Mapping):
            raise TypeError
        snapshot = {name: values[name] for name in names}
        if any(type(value) is not str for value in snapshot.values()):
            raise TypeError
        configuration = Auth0Configuration(
            domain=snapshot["CUEVION_AUTH0_DOMAIN"],
            client_id=snapshot["CUEVION_AUTH0_CLIENT_ID"],
            client_secret=snapshot["CUEVION_AUTH0_CLIENT_SECRET"],
            session_secret=snapshot["CUEVION_AUTH_SESSION_SECRET"],
        )
        return _validate_configuration(configuration)
    except Auth0FlowError:
        raise
    except Exception:
        _fail("invalid_configuration")


def _require_timestamp(value: object, *, error_code: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_UNIX_TIMESTAMP:
        _fail(error_code)
    return value


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(
    value: object,
    *,
    maximum_encoded_bytes: int,
    error_code: str,
) -> bytes:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum_encoded_bytes
        or not value.isascii()
        or _BASE64URL_RE.fullmatch(value) is None
        or len(value) % 4 == 1
    ):
        _fail(error_code)
    try:
        decoded = base64.b64decode(
            value.encode("ascii") + b"=" * ((-len(value)) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error):
        _fail(error_code)
    if _base64url_encode(decoded) != value:
        _fail(error_code)
    return decoded


def _random_bytes(
    generator: Callable[[int], bytes],
    count: int,
) -> bytes:
    try:
        value = generator(count)
    except Exception:
        _fail("internal_error")
    if type(value) is not bytes or len(value) != count:
        _fail("internal_error")
    return value


def _transaction_key(configuration: Auth0Configuration) -> bytes:
    validated = _validate_configuration(configuration)
    return hmac.new(
        validated.session_secret.encode("utf-8"),
        _TRANSACTION_KEY_DOMAIN,
        hashlib.sha256,
    ).digest()


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if type(name) is not str or name in result:
            raise ValueError
        result[name] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def _json_depth(value: object, depth: int = 0) -> int:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError
    if type(value) is dict:
        return max(
            (depth, *(_json_depth(item, depth + 1) for item in value.values()))
        )
    if type(value) is list:
        return max((depth, *(_json_depth(item, depth + 1) for item in value)))
    return depth


def _strict_json_bytes(
    body: object,
    *,
    maximum_bytes: int,
    error_code: str,
) -> object:
    if type(body) is not bytes or not body or len(body) > maximum_bytes:
        _fail(error_code)
    try:
        decoded = body.decode("utf-8", errors="strict")
        if decoded.startswith("\ufeff"):
            raise ValueError
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        _json_depth(value)
        return value
    except (UnicodeDecodeError, ValueError, RecursionError, json.JSONDecodeError):
        _fail(error_code)


def _is_exact_opaque_value(value: object) -> bool:
    if type(value) is not str or len(value) != 43:
        return False
    try:
        decoded = _base64url_decode(
            value,
            maximum_encoded_bytes=43,
            error_code="invalid_transaction",
        )
    except Auth0FlowError:
        return False
    return len(decoded) == _OPAQUE_VALUE_BYTES


def _new_transaction(
    *,
    state: object,
    nonce: object,
    code_verifier: object,
    issued_at: object,
    expires_at: object,
) -> AuthTransaction:
    if (
        not _is_exact_opaque_value(state)
        or not _is_exact_opaque_value(nonce)
        or type(code_verifier) is not str
        or _PKCE_RE.fullmatch(code_verifier) is None
        or not _is_exact_opaque_value(code_verifier)
        or type(issued_at) is not int
        or type(expires_at) is not int
        or not 0 <= issued_at < expires_at <= _MAX_UNIX_TIMESTAMP
        or expires_at - issued_at != AUTH_TRANSACTION_TTL_SECONDS
    ):
        _fail("invalid_transaction")
    return AuthTransaction(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _transaction_plaintext(transaction: AuthTransaction) -> bytes:
    value = {
        "code_verifier": transaction.code_verifier,
        "expires_at": transaction.expires_at,
        "issued_at": transaction.issued_at,
        "nonce": transaction.nonce,
        "state": transaction.state,
        "v": _TRANSACTION_VERSION,
    }
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_TRANSACTION_PLAINTEXT_BYTES:
        _fail("internal_error")
    return encoded


def _build_transaction_cookie(value: str) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or not value
        or len(value) > _MAX_COOKIE_VALUE_BYTES
    ):
        _fail("internal_error")
    return (
        f"{AUTH_TRANSACTION_COOKIE_NAME}={value}; Path=/; "
        f"Max-Age={AUTH_TRANSACTION_TTL_SECONDS}; Secure; HttpOnly; SameSite=Lax"
    )


def clear_transaction_cookie() -> str:
    """Return the fixed host-only transaction-cookie expiry header."""

    return (
        f"{AUTH_TRANSACTION_COOKIE_NAME}=; Path=/; Max-Age=0; "
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly; SameSite=Lax"
    )


def build_authorization_request(
    configuration: Auth0Configuration,
    now: int,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> AuthorizationRequest:
    """Create one Auth0 authorize URL and its encrypted PKCE transaction."""

    validated = _validate_configuration(configuration)
    issued_at = _require_timestamp(now, error_code="internal_error")
    if issued_at > _MAX_UNIX_TIMESTAMP - AUTH_TRANSACTION_TTL_SECONDS:
        _fail("internal_error")

    state = _base64url_encode(_random_bytes(random_bytes, _OPAQUE_VALUE_BYTES))
    nonce = _base64url_encode(_random_bytes(random_bytes, _OPAQUE_VALUE_BYTES))
    code_verifier = _base64url_encode(
        _random_bytes(random_bytes, _OPAQUE_VALUE_BYTES)
    )
    transaction = _new_transaction(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        issued_at=issued_at,
        expires_at=issued_at + AUTH_TRANSACTION_TTL_SECONDS,
    )
    code_challenge = _base64url_encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    )
    query = urlencode(
        (
            ("response_type", "code"),
            ("client_id", validated.client_id),
            ("redirect_uri", CALLBACK_URI),
            ("scope", _AUTHORIZATION_SCOPE),
            ("connection", _AUTHORIZATION_CONNECTION),
            ("code_challenge", code_challenge),
            ("code_challenge_method", "S256"),
            ("prompt", _AUTHORIZATION_PROMPT),
            ("state", state),
            ("nonce", nonce),
        )
    )
    authorization_url = f"{AUTH0_AUTHORIZE_ENDPOINT}?{query}"
    if len(authorization_url.encode("ascii")) > _MAX_AUTHORIZATION_URL_BYTES:
        _fail("invalid_configuration")

    encryption_nonce = _random_bytes(random_bytes, _TRANSACTION_NONCE_BYTES)
    try:
        ciphertext = AESGCM(_transaction_key(validated)).encrypt(
            encryption_nonce,
            _transaction_plaintext(transaction),
            _TRANSACTION_AAD,
        )
    except Auth0FlowError:
        raise
    except Exception:
        _fail("internal_error")
    cookie_value = ".".join(
        (
            _TRANSACTION_COOKIE_VERSION,
            _base64url_encode(encryption_nonce),
            _base64url_encode(ciphertext),
        )
    )
    return AuthorizationRequest(
        authorization_url=authorization_url,
        transaction_cookie=_build_transaction_cookie(cookie_value),
        transaction=transaction,
    )


def decrypt_transaction_cookie(
    cookie_value: object,
    configuration: Auth0Configuration,
    now: int,
) -> AuthTransaction:
    """Authenticate, decrypt, and time-bound one transaction cookie value."""

    validated = _validate_configuration(configuration)
    current_time = _require_timestamp(now, error_code="invalid_transaction")
    if (
        type(cookie_value) is not str
        or not cookie_value.isascii()
        or not cookie_value
        or len(cookie_value) > _MAX_COOKIE_VALUE_BYTES
    ):
        _fail("invalid_transaction")
    parts = cookie_value.split(".")
    if len(parts) != 3 or parts[0] != _TRANSACTION_COOKIE_VERSION:
        _fail("invalid_transaction")
    encryption_nonce = _base64url_decode(
        parts[1],
        maximum_encoded_bytes=32,
        error_code="invalid_transaction",
    )
    ciphertext = _base64url_decode(
        parts[2],
        maximum_encoded_bytes=3_072,
        error_code="invalid_transaction",
    )
    if len(encryption_nonce) != _TRANSACTION_NONCE_BYTES or len(ciphertext) < 17:
        _fail("invalid_transaction")
    try:
        plaintext = AESGCM(_transaction_key(validated)).decrypt(
            encryption_nonce,
            ciphertext,
            _TRANSACTION_AAD,
        )
    except (InvalidTag, ValueError, TypeError):
        _fail("invalid_transaction")
    if len(plaintext) > _MAX_TRANSACTION_PLAINTEXT_BYTES:
        _fail("invalid_transaction")
    payload = _strict_json_bytes(
        plaintext,
        maximum_bytes=_MAX_TRANSACTION_PLAINTEXT_BYTES,
        error_code="invalid_transaction",
    )
    if type(payload) is not dict or set(payload) != {
        "code_verifier",
        "expires_at",
        "issued_at",
        "nonce",
        "state",
        "v",
    }:
        _fail("invalid_transaction")
    if type(payload["v"]) is not int or payload["v"] != _TRANSACTION_VERSION:
        _fail("invalid_transaction")
    transaction = _new_transaction(
        state=payload["state"],
        nonce=payload["nonce"],
        code_verifier=payload["code_verifier"],
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
    )
    if not transaction.issued_at <= current_time < transaction.expires_at:
        _fail("invalid_transaction")
    return transaction


def consume_transaction_cookie(
    cookie_value: object,
    returned_state: object,
    configuration: Auth0Configuration,
    now: int,
) -> AuthTransaction:
    """Resolve a transaction and compare callback state in constant time."""

    transaction = decrypt_transaction_cookie(cookie_value, configuration, now)
    if (
        type(returned_state) is not str
        or not _is_exact_opaque_value(returned_state)
        or not hmac.compare_digest(transaction.state, returned_state)
    ):
        _fail("invalid_transaction")
    return transaction


def _validate_code(value: object) -> str:
    if not _is_bounded_visible_ascii(value, _MAX_CODE_BYTES):
        _fail("invalid_transaction")
    return value


def _validate_pkce_verifier(value: object) -> str:
    if type(value) is not str or _PKCE_RE.fullmatch(value) is None:
        _fail("invalid_transaction")
    return value


def build_token_exchange_request(
    configuration: Auth0Configuration,
    code: str,
    code_verifier: str,
) -> OutboundRequest:
    """Build the one allowed token-endpoint request; the secret stays in body."""

    validated = _validate_configuration(configuration)
    safe_code = _validate_code(code)
    safe_verifier = _validate_pkce_verifier(code_verifier)
    body = urlencode(
        (
            ("grant_type", "authorization_code"),
            ("client_id", validated.client_id),
            ("client_secret", validated.client_secret),
            ("code", safe_code),
            ("redirect_uri", CALLBACK_URI),
            ("code_verifier", safe_verifier),
        )
    ).encode("ascii")
    if len(body) > 16_384:
        _fail("invalid_configuration")
    return OutboundRequest(
        url=AUTH0_TOKEN_ENDPOINT,
        method="POST",
        headers=(
            ("Accept", "application/json"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", str(len(body))),
        ),
        body=body,
        timeout_seconds=_AUTH0_TIMEOUT_SECONDS,
        max_response_bytes=_MAX_TOKEN_RESPONSE_BYTES,
    )


def build_jwks_request() -> OutboundRequest:
    """Build the one allowed Auth0 JWKS request."""

    return OutboundRequest(
        url=AUTH0_JWKS_ENDPOINT,
        method="GET",
        headers=(("Accept", "application/json"),),
        body=None,
        timeout_seconds=_AUTH0_TIMEOUT_SECONDS,
        max_response_bytes=_MAX_JWKS_RESPONSE_BYTES,
    )


def build_logout_url(configuration: Auth0Configuration) -> str:
    """Build the fixed Auth0 logout URL with no caller-controlled return target."""

    validated = _validate_configuration(configuration)
    return f"{AUTH0_LOGOUT_ENDPOINT}?{urlencode((('client_id', validated.client_id), ('returnTo', LOGOUT_RETURN_TO)))}"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


def _response_headers(value: object) -> tuple[tuple[str, str], ...]:
    try:
        raw_items = value.raw_items  # type: ignore[attr-defined]
        result = tuple(raw_items()) if callable(raw_items) else tuple(value.items())  # type: ignore[attr-defined]
    except Exception:
        _fail("provider_unavailable")
    if len(result) > 64:
        _fail("provider_unavailable")
    validated: list[tuple[str, str]] = []
    total = 0
    for pair in result:
        if type(pair) not in (list, tuple) or len(pair) != 2:
            _fail("provider_unavailable")
        name, header_value = pair
        if (
            type(name) is not str
            or type(header_value) is not str
            or not name.isascii()
            or not header_value.isascii()
            or any(ord(character) <= 31 or ord(character) == 127 for character in header_value)
        ):
            _fail("provider_unavailable")
        total += len(name) + len(header_value)
        if total > 32_768:
            _fail("provider_unavailable")
        validated.append((name, header_value))
    return tuple(validated)


def urllib_transport(request: OutboundRequest) -> OutboundResponse:
    """Execute one bounded HTTPS request without following redirects."""

    if type(request) is not OutboundRequest:
        _fail("provider_unavailable")
    if request.url not in {AUTH0_TOKEN_ENDPOINT, AUTH0_JWKS_ENDPOINT}:
        _fail("provider_unavailable")
    opener = build_opener(_NoRedirectHandler())
    outbound = Request(
        request.url,
        data=request.body,
        headers=dict(request.headers),
        method=request.method,
    )
    try:
        with opener.open(outbound, timeout=request.timeout_seconds) as response:
            body = response.read(request.max_response_bytes + 1)
            return OutboundResponse(
                status=response.status,
                url=response.geturl(),
                headers=_response_headers(response.headers),
                body=body,
            )
    except HTTPError as error:
        try:
            body = error.read(request.max_response_bytes + 1)
            headers = _response_headers(error.headers)
            url = error.geturl()
        except Exception:
            _fail("provider_unavailable")
        return OutboundResponse(error.code, url, headers, body)
    except (URLError, TimeoutError, OSError, ValueError):
        _fail("provider_unavailable")


def _perform_outbound_request(
    request: OutboundRequest,
    transport: Callable[[OutboundRequest], OutboundResponse],
) -> bytes:
    try:
        response = transport(request)
    except Auth0FlowError:
        raise
    except Exception:
        _fail("provider_unavailable")
    if (
        type(response) is not OutboundResponse
        or type(response.status) is not int
        or response.status != 200
        or response.url != request.url
        or type(response.headers) is not tuple
        or type(response.body) is not bytes
        or not response.body
        or len(response.body) > request.max_response_bytes
    ):
        _fail("provider_unavailable")
    content_types: list[str] = []
    for pair in response.headers:
        if type(pair) is not tuple or len(pair) != 2:
            _fail("provider_unavailable")
        name, value = pair
        if type(name) is not str or type(value) is not str:
            _fail("provider_unavailable")
        if name.lower() == "content-type":
            content_types.append(value)
    if len(content_types) != 1 or content_types[0].lower() not in {
        "application/json",
        "application/json; charset=utf-8",
    }:
        _fail("provider_unavailable")
    return response.body


def parse_token_response(body: bytes) -> TokenResponse:
    """Parse a bounded token response while retaining only the ID token."""

    payload = _strict_json_bytes(
        body,
        maximum_bytes=_MAX_TOKEN_RESPONSE_BYTES,
        error_code="invalid_token_response",
    )
    allowed = {"access_token", "expires_in", "id_token", "scope", "token_type"}
    if type(payload) is not dict or not set(payload).issubset(allowed):
        _fail("invalid_token_response")
    id_token = payload.get("id_token")
    if (
        type(id_token) is not str
        or not id_token.isascii()
        or not id_token
        or len(id_token) > _MAX_ID_TOKEN_BYTES
        or len(id_token.split(".")) != 3
    ):
        _fail("invalid_token_response")
    access_token = payload.get("access_token")
    scope = payload.get("scope")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    if (
        (access_token is not None and not _is_bounded_visible_ascii(access_token, 65_536))
        or (scope is not None and (type(scope) is not str or not 1 <= len(scope) <= 4_096))
        or (token_type is not None and token_type != "Bearer")
        or (
            expires_in is not None
            and (type(expires_in) is not int or not 1 <= expires_in <= 86_400)
        )
    ):
        _fail("invalid_token_response")
    return TokenResponse(id_token=id_token)


def exchange_authorization_code(
    configuration: Auth0Configuration,
    code: str,
    code_verifier: str,
    transport: Callable[[OutboundRequest], OutboundResponse] = urllib_transport,
) -> TokenResponse:
    """Exchange one code at the exact token endpoint through a bounded transport."""

    request = build_token_exchange_request(configuration, code, code_verifier)
    body = _perform_outbound_request(request, transport)
    return parse_token_response(body)


def _jwt_segments(id_token: object) -> tuple[str, str, str]:
    if (
        type(id_token) is not str
        or not id_token.isascii()
        or not id_token
        or len(id_token) > _MAX_ID_TOKEN_BYTES
    ):
        _fail("invalid_id_token")
    segments = id_token.split(".")
    if len(segments) != 3:
        _fail("invalid_id_token")
    for segment in segments:
        if not segment or _BASE64URL_RE.fullmatch(segment) is None:
            _fail("invalid_id_token")
    return segments[0], segments[1], segments[2]


def _jwt_header(segment: str) -> dict[str, object]:
    raw = _base64url_decode(
        segment,
        maximum_encoded_bytes=2_048,
        error_code="invalid_id_token",
    )
    header = _strict_json_bytes(
        raw,
        maximum_bytes=1_024,
        error_code="invalid_id_token",
    )
    if type(header) is not dict or set(header) != {"alg", "kid", "typ"}:
        _fail("invalid_id_token")
    if (
        header.get("alg") != "RS256"
        or header.get("typ") != "JWT"
        or not _is_bounded_visible_ascii(header.get("kid"), 256)
    ):
        _fail("invalid_id_token")
    return header


def _matching_jwk(jwks_body: bytes, kid: str) -> dict[str, object]:
    payload = _strict_json_bytes(
        jwks_body,
        maximum_bytes=_MAX_JWKS_RESPONSE_BYTES,
        error_code="invalid_jwks",
    )
    if type(payload) is not dict or set(payload) != {"keys"}:
        _fail("invalid_jwks")
    keys = payload.get("keys")
    if type(keys) is not list or not 1 <= len(keys) <= _MAX_JWKS_KEYS:
        _fail("invalid_jwks")
    matches = tuple(
        key for key in keys if type(key) is dict and key.get("kid") == kid
    )
    if len(matches) != 1:
        _fail("invalid_jwks")
    key = matches[0]
    required = {"alg", "e", "kid", "kty", "n", "use"}
    allowed = required | {"key_ops", "x5c", "x5t", "x5t#S256"}
    if not required.issubset(key) or not set(key).issubset(allowed):
        _fail("invalid_jwks")
    if (
        key.get("alg") != "RS256"
        or key.get("kty") != "RSA"
        or key.get("use") != "sig"
        or key.get("kid") != kid
    ):
        _fail("invalid_jwks")
    key_ops = key.get("key_ops")
    if key_ops is not None and key_ops != ["verify"]:
        _fail("invalid_jwks")
    return key


def _rsa_public_key(jwk: dict[str, object]):
    modulus_bytes = _base64url_decode(
        jwk.get("n"),
        maximum_encoded_bytes=2_048,
        error_code="invalid_jwks",
    )
    exponent_bytes = _base64url_decode(
        jwk.get("e"),
        maximum_encoded_bytes=8,
        error_code="invalid_jwks",
    )
    if (
        not 256 <= len(modulus_bytes) <= _MAX_JWK_MODULUS_BYTES
        or modulus_bytes[0] == 0
        or not 1 <= len(exponent_bytes) <= 4
        or exponent_bytes[0] == 0
    ):
        _fail("invalid_jwks")
    modulus = int.from_bytes(modulus_bytes, "big", signed=False)
    exponent = int.from_bytes(exponent_bytes, "big", signed=False)
    if (
        modulus.bit_length() < _MIN_JWK_MODULUS_BITS
        or exponent < 3
        or exponent > 4_294_967_295
        or exponent % 2 == 0
    ):
        _fail("invalid_jwks")
    try:
        return rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except (ValueError, TypeError):
        _fail("invalid_jwks")


def _canonical_email(value: object) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or value != value.lower()
        or not 3 <= len(value) <= 320
        or _EMAIL_RE.fullmatch(value) is None
    ):
        _fail("invalid_id_token")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or len(domain) > 253:
        _fail("invalid_id_token")
    return value


def _validated_claims(
    payload: object,
    configuration: Auth0Configuration,
    expected_nonce: object,
    now: int,
) -> ValidatedIdentityEvidence:
    if type(payload) is not dict:
        _fail("invalid_id_token")
    if not _is_exact_opaque_value(expected_nonce):
        _fail("invalid_transaction")
    current_time = _require_timestamp(now, error_code="invalid_id_token")
    issuer = payload.get("iss")
    audience = payload.get("aud")
    subject = payload.get("sub")
    email = payload.get("email")
    email_verified = payload.get("email_verified")
    nonce = payload.get("nonce")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    not_before = payload.get("nbf")
    authorized_party = payload.get("azp")
    authentication_time = payload.get("auth_time")
    if (
        issuer != AUTH0_ISSUER
        or audience != configuration.client_id
        or not _is_bounded_visible_ascii(subject, 512)
        or not _is_exact_opaque_value(nonce)
        or not hmac.compare_digest(nonce, expected_nonce)
        or type(email_verified) is not bool
        or email_verified is not True
        or type(issued_at) is not int
        or type(expires_at) is not int
        or not 0 <= issued_at < expires_at <= _MAX_UNIX_TIMESTAMP
        or current_time >= expires_at
        or issued_at > current_time + _MAX_CLOCK_SKEW_SECONDS
        or current_time - issued_at > _MAX_ID_TOKEN_AGE_SECONDS
        or expires_at - issued_at > _MAX_ID_TOKEN_AGE_SECONDS
        or (
            not_before is not None
            and (
                type(not_before) is not int
                or not 0 <= not_before <= _MAX_UNIX_TIMESTAMP
                or not_before > current_time + _MAX_CLOCK_SKEW_SECONDS
            )
        )
        or (authorized_party is not None and authorized_party != configuration.client_id)
        or (
            authentication_time is not None
            and (
                type(authentication_time) is not int
                or not 0 <= authentication_time <= current_time + _MAX_CLOCK_SKEW_SECONDS
            )
        )
    ):
        _fail("invalid_id_token")
    return ValidatedIdentityEvidence(
        issuer=issuer,
        subject=subject,
        email=_canonical_email(email),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def validate_id_token(
    id_token: str,
    jwks_body: bytes,
    configuration: Auth0Configuration,
    expected_nonce: str,
    now: int,
) -> ValidatedIdentityEvidence:
    """Verify an Auth0 RS256 ID token and return only trusted identity evidence."""

    validated = _validate_configuration(configuration)
    header_segment, payload_segment, signature_segment = _jwt_segments(id_token)
    header = _jwt_header(header_segment)
    jwk = _matching_jwk(jwks_body, header["kid"])  # type: ignore[arg-type]
    public_key = _rsa_public_key(jwk)
    signature = _base64url_decode(
        signature_segment,
        maximum_encoded_bytes=2_048,
        error_code="invalid_id_token",
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    try:
        public_key.verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        _fail("invalid_id_token")
    except Exception:
        _fail("invalid_id_token")

    payload_bytes = _base64url_decode(
        payload_segment,
        maximum_encoded_bytes=_MAX_ID_TOKEN_BYTES,
        error_code="invalid_id_token",
    )
    payload = _strict_json_bytes(
        payload_bytes,
        maximum_bytes=_MAX_ID_TOKEN_BYTES,
        error_code="invalid_id_token",
    )
    return _validated_claims(payload, validated, expected_nonce, now)


def validate_id_token_with_jwks(
    id_token: str,
    configuration: Auth0Configuration,
    expected_nonce: str,
    now: int,
    transport: Callable[[OutboundRequest], OutboundResponse] = urllib_transport,
) -> ValidatedIdentityEvidence:
    """Fetch Auth0 JWKS through the bounded transport and validate one ID token."""

    body = _perform_outbound_request(build_jwks_request(), transport)
    return validate_id_token(id_token, body, configuration, expected_nonce, now)


__all__ = (
    "AUTH0_DOMAIN",
    "AUTH0_ISSUER",
    "AUTH0_AUTHORIZE_ENDPOINT",
    "AUTH0_TOKEN_ENDPOINT",
    "AUTH0_JWKS_ENDPOINT",
    "AUTH0_LOGOUT_ENDPOINT",
    "CALLBACK_URI",
    "LOGOUT_RETURN_TO",
    "AUTH_TRANSACTION_COOKIE_NAME",
    "SESSION_COOKIE_NAME",
    "AUTH_TRANSACTION_TTL_SECONDS",
    "SESSION_TTL_SECONDS",
    "Auth0FlowError",
    "Auth0Configuration",
    "AuthTransaction",
    "AuthorizationRequest",
    "OutboundRequest",
    "OutboundResponse",
    "TokenResponse",
    "ValidatedIdentityEvidence",
    "parse_auth0_configuration",
    "build_authorization_request",
    "decrypt_transaction_cookie",
    "consume_transaction_cookie",
    "clear_transaction_cookie",
    "build_token_exchange_request",
    "build_jwks_request",
    "build_logout_url",
    "urllib_transport",
    "parse_token_response",
    "exchange_authorization_code",
    "validate_id_token",
    "validate_id_token_with_jwks",
)
