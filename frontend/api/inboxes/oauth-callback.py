import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import tempfile
import time
from copy import deepcopy
from http import HTTPStatus
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
OAUTH_CALLBACK_PATH = "/api/inboxes/oauth-callback"
PUBLIC_APP_ORIGIN_ENV = "CUEVION_APP_URL"
PRODUCTION_APP_ORIGIN = "https://app.cuevion.com"
LOCAL_APP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MICROSOFT_TOKEN_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
)
STATE_MAX_AGE_SECONDS = 15 * 60
MAX_OAUTH_RESPONSE_BYTES = 256 * 1024
USER_CONFIG_SCHEMA_VERSION = 1
MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS = 3
MAX_GMAIL_TOKEN_WRITE_ATTEMPTS = 3
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
OAUTH_STATE_VERSION = 3
MAX_STATE_CLOCK_SKEW_SECONDS = 60
STATE_SIGNATURE_DOMAIN = "cuevion-oauth-state-signature:v3"
OWNER_BINDING_DOMAIN = "cuevion-oauth-owner-binding:v3"
PKCE_DERIVATION_DOMAIN = "cuevion-oauth-pkce:v3"
OAUTH_CREDENTIAL_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
OAUTH_MAILBOX_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
ONBOARDING_PRESET_INBOX_IDS = {
    "main",
    "demo",
    "business",
    "promo",
    "legal",
    "finance",
    "royalty",
    "sync",
}
ONBOARDING_CUSTOM_INBOX_ID_PATTERN = re.compile(
    r"^custom:[a-z0-9]+(?:-[a-z0-9]+)*$"
)
GMAIL_CALLBACK_FAILURE_CODES = frozenset(
    {
        "authorization_code_missing",
        "canonical_origin_invalid",
        "gmail_link_conflict",
        "google_identity_invalid",
        "google_identity_unavailable",
        "mailbox_readback_verification_failed",
        "member_authority_unavailable",
        "member_unauthenticated",
        "owner_binding_invalid",
        "oauth_reconnect_email_mismatch",
        "oauth_reconnect_in_progress",
        "oauth_reconnect_stale",
        "oauth_reconnect_target_invalid",
        "provider_denied",
        "state_expired",
        "state_invalid",
        "token_exchange_failed",
        "token_exchange_unavailable",
        "token_email_mismatch",
        "token_legacy_owner_equals_mailbox",
        "token_owner_conflict",
        "token_owner_fields_empty",
        "token_owner_fields_partial",
        "token_owner_mismatch",
        "token_payload_invalid",
        "token_persistence_failed",
        "token_provider_mismatch",
        "refresh_token_missing",
        "token_record_malformed",
        "token_store_unavailable",
        "unexpected_callback_failure",
        "user_config_invalid",
        "user_config_preflight_failed",
        "user_config_readback_failed",
        "user_config_store_unavailable",
        "user_config_write_failed",
    }
)
GMAIL_CALLBACK_FAILURE_CODE_FIELD = "_gmail_callback_failure_code"
_GMAIL_CALLBACK_LOGGER = logging.getLogger(__name__)
_MEMBER_AUTHORITY_UNAVAILABLE = object()
GOOGLE_TOKEN_RECORD_ABSENT = "ABSENT"
GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH = "EXACT_OWNER_MATCH"
GOOGLE_TOKEN_RECORD_LEGACY_OWNERLESS_MATCH = "LEGACY_OWNERLESS_MATCH"
GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH = (
    "LEGACY_OWNER_EQUALS_MAILBOX_MATCH"
)
GOOGLE_TOKEN_RECORD_OWNER_MISMATCH = "OWNER_MISMATCH"
GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH = "PROVIDER_OR_EMAIL_MISMATCH"
GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS = "MALFORMED_OR_AMBIGUOUS"
LEGACY_GOOGLE_TOKEN_RECORD_FIELDS = frozenset(
    {
        "provider",
        "email",
        "access_token",
        "refresh_token",
        "token_type",
        "scope",
        "expires_at",
        "expires_in",
        "updated_at",
        "created_at",
    }
)
PRE_GENERATION_GOOGLE_TOKEN_RECORD_FIELDS = frozenset(
    {*LEGACY_GOOGLE_TOKEN_RECORD_FIELDS, "owner_email"}
)
CURRENT_GOOGLE_TOKEN_RECORD_FIELDS = frozenset(
    {*PRE_GENERATION_GOOGLE_TOKEN_RECORD_FIELDS, "credential_generation"}
)
ADOPTABLE_LEGACY_GOOGLE_TOKEN_RECORD_MATCHES = frozenset(
    {
        GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH,
    }
)
GOOGLE_TOKEN_OWNER_IDENTITY_FIELDS = frozenset(
    {"provider", "email", "owner_email"}
)
LEGACY_GOOGLE_TOKEN_ADOPTION_SCRIPT = (
    "local current=redis.call('GET',KEYS[1]);"
    "if current~=ARGV[1] then return 0 end;"
    "redis.call('SET',KEYS[1],ARGV[2]);"
    "return 1"
)
GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT = (
    "if redis.call('EXISTS',KEYS[1])~=0 then return 0 end;"
    "redis.call('SET',KEYS[1],ARGV[1]);"
    "return 1"
)
GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT = (
    "local current=redis.call('GET',KEYS[1]);"
    "if current~=ARGV[1] then return 0 end;"
    "redis.call('SET',KEYS[1],ARGV[2]);"
    "return 1"
)
GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX = "cuevion-google-token-raw:v1:"
GOOGLE_TOKEN_READ_EXACT_SCRIPT = (
    "local current=redis.call('GET',KEYS[1]);"
    "if not current then return false end;"
    f"return '{GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX}'..current"
)


def _log_gmail_callback_failure(
    failure_code: str,
    inbox_position: str | None = None,
) -> None:
    safe_failure_code = (
        failure_code
        if isinstance(failure_code, str)
        and failure_code in GMAIL_CALLBACK_FAILURE_CODES
        else "unexpected_callback_failure"
    )
    safe_inbox_position = (
        inbox_position
        if isinstance(inbox_position, str)
        and (
            inbox_position in ONBOARDING_PRESET_INBOX_IDS
            or ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(inbox_position)
            is not None
        )
        else None
    )
    fields = [
        "event=gmail_oauth_callback_failure",
        f"failure_code={safe_failure_code}",
        "provider=google",
    ]
    if safe_inbox_position is not None:
        fields.append(f"inbox_position={safe_inbox_position}")
    try:
        _GMAIL_CALLBACK_LOGGER.warning(" ".join(fields))
    except Exception:
        return


def _resolve_gmail_callback_failure_code(
    error: dict | None,
    *,
    default: str,
    code_mapping: dict[str, str] | None = None,
) -> str:
    diagnostic_code = (
        error.get(GMAIL_CALLBACK_FAILURE_CODE_FIELD)
        if isinstance(error, dict)
        else None
    )
    if (
        isinstance(diagnostic_code, str)
        and diagnostic_code in GMAIL_CALLBACK_FAILURE_CODES
    ):
        return diagnostic_code

    public_code = error.get("code") if isinstance(error, dict) else None
    mapped_code = (
        (code_mapping or {}).get(public_code)
        if isinstance(public_code, str)
        else None
    )
    if mapped_code in GMAIL_CALLBACK_FAILURE_CODES:
        return mapped_code
    return default


def _send_gmail_callback_failure(
    request,
    payload: dict,
    *,
    failure_code: str,
    set_cookies: tuple[str, ...] | None = None,
    inbox_position: str | None = None,
) -> None:
    if payload.get("provider") == "google":
        callback_mode = getattr(request, "_oauth_callback_mode", None)
        callback_mailbox_id = getattr(request, "_oauth_callback_mailbox_id", None)
        callback_expected_email = getattr(
            request,
            "_oauth_callback_expected_email",
            None,
        )
        if callback_mode in {"initial", "reconnect"}:
            payload.setdefault("mode", callback_mode)
        if (
            callback_mode == "reconnect"
            and isinstance(callback_mailbox_id, str)
            and OAUTH_MAILBOX_ID_PATTERN.fullmatch(callback_mailbox_id)
        ):
            payload.setdefault("mailboxId", callback_mailbox_id)
        if (
            callback_mode == "reconnect"
            and isinstance(callback_expected_email, str)
            and EMAIL_PATTERN.fullmatch(callback_expected_email)
        ):
            payload.setdefault("email", callback_expected_email)
    if payload.get("provider") == "google" and not getattr(
        request,
        "_gmail_callback_failure_logged",
        False,
    ):
        request._gmail_callback_failure_logged = True
        _log_gmail_callback_failure(failure_code, inbox_position)
    if set_cookies is None:
        request._send_callback_page(payload)
    else:
        request._send_callback_page(payload, set_cookies=set_cookies)


def _deployment_environment() -> str:
    return os.getenv("VERCEL_ENV", "").strip().lower()


def _is_production_environment() -> bool:
    return _deployment_environment() == "production"


def _normalize_public_app_origin(
    value: str,
    *,
    production: bool,
) -> str | None:
    candidate = value.strip()
    if (
        not candidate
        or "?" in candidate
        or "#" in candidate
        or "\\" in candidate
        or any(character.isspace() for character in candidate)
    ):
        return None

    if candidate.endswith("/"):
        candidate = candidate[:-1]

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None

    hostname = parsed.hostname.lower() if isinstance(parsed.hostname, str) else ""
    if (
        not hostname
        or "*" in hostname
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    scheme = parsed.scheme.lower()
    is_local = hostname in LOCAL_APP_HOSTS
    if production:
        if scheme != "https" or hostname != "app.cuevion.com" or port is not None:
            return None
    elif scheme != "https" and not (scheme == "http" and is_local):
        return None

    normalized_host = f"[{hostname}]" if ":" in hostname else hostname
    normalized_netloc = (
        f"{normalized_host}:{port}" if port is not None else normalized_host
    )
    if parsed.netloc.lower() != normalized_netloc.lower():
        return None

    normalized_origin = f"{scheme}://{normalized_netloc}"
    if production and normalized_origin != PRODUCTION_APP_ORIGIN:
        return None
    return normalized_origin


def resolve_public_app_origin(headers=None) -> str | None:
    deployment_environment = _deployment_environment()
    production = _is_production_environment()
    configured_origin = os.getenv(PUBLIC_APP_ORIGIN_ENV, "")
    if configured_origin.strip():
        normalized_origin = _normalize_public_app_origin(
            configured_origin,
            production=production,
        )
        if not normalized_origin:
            return None
        if normalized_origin == PRODUCTION_APP_ORIGIN:
            return normalized_origin

        parsed_origin = urlsplit(normalized_origin)
        if (
            deployment_environment == "preview"
            and parsed_origin.scheme == "https"
            and parsed_origin.hostname not in LOCAL_APP_HOSTS
        ):
            return normalized_origin
        if (
            deployment_environment in {"", "development"}
            and parsed_origin.hostname in LOCAL_APP_HOSTS
        ):
            return normalized_origin
        return None

    if deployment_environment not in {"", "development"} or headers is None:
        return None

    request_host = headers.get("host")
    if not isinstance(request_host, str) or not request_host.strip():
        return None

    forwarded_protocol = headers.get("x-forwarded-proto")
    protocol = (
        forwarded_protocol.strip().lower()
        if isinstance(forwarded_protocol, str) and forwarded_protocol.strip()
        else "http"
    )
    local_origin = _normalize_public_app_origin(
        f"{protocol}://{request_host.strip()}",
        production=False,
    )
    if not local_origin:
        return None
    parsed_local_origin = urlsplit(local_origin)
    if parsed_local_origin.hostname not in LOCAL_APP_HOSTS:
        return None
    return local_origin


def resolve_google_redirect_uri(headers=None) -> str | None:
    public_origin = resolve_public_app_origin(headers)
    if not public_origin:
        return None
    return f"{public_origin}{OAUTH_CALLBACK_PATH}"

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from oauth_token_store import persist_microsoft_token_record
from api import user_config_store
from api.auth import http, runtime
from api.auth.email_address import normalize_auth_email


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def build_owner_binding(
    *,
    owner_email: str,
    member_user_id: str,
    member_workspace_id: str,
    provider: str,
    email_hint: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
    signing_secret: str,
    inbox_position: str | None = None,
    mode: str = "initial",
    mailbox_id: str | None = None,
    expected_email: str | None = None,
    credential_generation: str | None = None,
) -> str:
    normalized_user_id = (
        member_user_id.strip() if isinstance(member_user_id, str) else ""
    )
    normalized_workspace_id = (
        member_workspace_id.strip()
        if isinstance(member_workspace_id, str)
        else ""
    )
    if not normalized_user_id or not normalized_workspace_id:
        raise ValueError("Authenticated member context is required.")
    binding_fields = [
        OWNER_BINDING_DOMAIN,
        str(OAUTH_STATE_VERSION),
        normalize_auth_email(owner_email),
        normalized_user_id,
        normalized_workspace_id,
        provider,
        email_hint,
        mode,
        credential_generation or "",
    ]
    if mode == "reconnect":
        binding_fields.extend((mailbox_id or "", expected_email or ""))
    if inbox_position is not None:
        binding_fields.append(inbox_position)
    binding_fields.extend((nonce, str(issued_at), str(expires_at)))
    binding_message = json.dumps(
        binding_fields,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            binding_message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def verify_owner_binding(
    payload: dict,
    owner_email: str,
    signing_secret: str,
    *,
    member_user_id: str,
    member_workspace_id: str,
) -> bool:
    try:
        expected_binding = build_owner_binding(
            owner_email=owner_email,
            member_user_id=member_user_id,
            member_workspace_id=member_workspace_id,
            provider=payload["provider"],
            email_hint=payload["email_hint"],
            nonce=payload["nonce"],
            issued_at=payload["issued_at"],
            expires_at=payload["expires_at"],
            signing_secret=signing_secret,
            inbox_position=payload.get("inboxPosition"),
            mode=payload["mode"],
            mailbox_id=payload.get("mailboxId"),
            expected_email=payload.get("expected_email"),
            credential_generation=payload["credential_generation"],
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(payload["owner_binding"], expected_binding)


def verify_signed_state(
    state: str,
    signing_secret: str,
    expected_provider: str | None = "google",
) -> tuple[dict | None, str | None]:
    if not state or "." not in state:
        return None, "invalid_state"

    encoded_payload, signature = state.split(".", 1)
    expected_signature = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{STATE_SIGNATURE_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )

    if not hmac.compare_digest(signature, expected_signature):
        return None, "invalid_state"

    try:
        payload = json.loads(base64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid_state"

    if not isinstance(payload, dict):
        return None, "invalid_state"
    allowed_state_fields = {
        "v",
        "provider",
        "email_hint",
        "mode",
        "credential_generation",
        "issued_at",
        "expires_at",
        "nonce",
        "owner_binding",
        "inboxPosition",
        "mailboxId",
        "expected_email",
    }
    required_state_fields = {
        "v",
        "provider",
        "email_hint",
        "mode",
        "credential_generation",
        "issued_at",
        "expires_at",
        "nonce",
        "owner_binding",
    }
    if set(payload) - allowed_state_fields or not required_state_fields.issubset(payload):
        return None, "invalid_state"
    if expected_provider is not None and payload.get("provider") != expected_provider:
        return None, "invalid_state"
    if payload.get("provider") not in {"google", "microsoft"}:
        return None, "invalid_state"

    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    current_time = int(time.time())
    if (
        payload.get("v") != OAUTH_STATE_VERSION
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or issued_at > current_time + MAX_STATE_CLOCK_SKEW_SECONDS
        or expires_at <= issued_at
        or expires_at - issued_at > STATE_MAX_AGE_SECONDS
    ):
        return None, "invalid_state"

    if current_time >= expires_at:
        return None, "expired_state"

    email_hint = payload.get("email_hint")
    inbox_position = payload.get("inboxPosition")
    mode = payload.get("mode")
    mailbox_id = payload.get("mailboxId")
    expected_email = payload.get("expected_email")
    credential_generation = payload.get("credential_generation")
    nonce = payload.get("nonce")
    owner_binding = payload.get("owner_binding")
    if (
        not isinstance(email_hint, str)
        or email_hint != email_hint.strip().lower()
        or (email_hint and not EMAIL_PATTERN.match(email_hint))
        or mode not in {"initial", "reconnect"}
        or not isinstance(credential_generation, str)
        or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation)
        is None
        or (
            inbox_position is not None
            and (
                not isinstance(inbox_position, str)
                or (
                    inbox_position not in ONBOARDING_PRESET_INBOX_IDS
                    and ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(inbox_position)
                    is None
                )
            )
        )
        or not isinstance(nonce, str)
        or not 16 <= len(nonce) <= 128
        or not isinstance(owner_binding, str)
        or len(owner_binding) != 43
        or "owner_email" in payload
    ):
        return None, "invalid_state"

    if mode == "reconnect":
        if (
            payload.get("provider") != "google"
            or not isinstance(mailbox_id, str)
            or OAUTH_MAILBOX_ID_PATTERN.fullmatch(mailbox_id) is None
            or not isinstance(expected_email, str)
            or expected_email != expected_email.strip().lower()
            or EMAIL_PATTERN.fullmatch(expected_email) is None
            or email_hint != expected_email
            or inbox_position is not None
        ):
            return None, "invalid_state"
    elif mailbox_id is not None or expected_email is not None:
        return None, "invalid_state"

    payload["code_verifier"] = base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            f"{PKCE_DERIVATION_DOMAIN}:{encoded_payload}".encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )

    return payload, None


def _resolve_runtime_store_path() -> Path:
    configured_path = os.getenv("CUEVION_GMAIL_TOKEN_STORE_PATH", "").strip()
    if configured_path:
        return Path(configured_path)

    return Path(tempfile.gettempdir()) / "cuevion-gmail-oauth-token-store.json"


def _resolve_durable_store_config() -> dict | None:
    rest_url = os.getenv("KV_REST_API_URL", "").strip()
    rest_token = os.getenv("KV_REST_API_TOKEN", "").strip()

    if not rest_url or not rest_token:
        return None

    return {
        "backend": "vercel_kv_rest",
        "rest_url": rest_url.rstrip("/"),
        "rest_token": rest_token,
    }


def _read_runtime_store(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _write_runtime_store(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with temp_path.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def _resolve_expiry(token_payload: dict) -> tuple[str | None, int | None]:
    expires_in = token_payload.get("expires_in")
    if isinstance(expires_in, str):
        try:
            expires_in = int(expires_in)
        except ValueError:
            expires_in = None

    if not isinstance(expires_in, int) or expires_in <= 0:
        return None, None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return expires_at.isoformat(), expires_in


def _build_store_key(state_or_mailbox_id: str) -> str:
    return f"cuevion:gmail:oauthtoken:{state_or_mailbox_id.strip().lower()}"


def _is_canonical_token_email(value) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip().lower()
        and EMAIL_PATTERN.fullmatch(value) is not None
    )


def _is_supported_token_timestamp(value) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _has_supported_google_token_record_shape(
    record: dict,
    expected_fields: frozenset[str],
) -> bool:
    if frozenset(record) != expected_fields:
        return False

    access_token = record.get("access_token")
    refresh_token = record.get("refresh_token")
    token_type = record.get("token_type")
    scope = record.get("scope")
    expires_at = record.get("expires_at")
    expires_in = record.get("expires_in")
    created_at = record.get("created_at")
    updated_at = record.get("updated_at")
    credential_generation = record.get("credential_generation")
    expiry_is_supported = (
        expires_at is None
        and expires_in is None
        or _is_supported_token_timestamp(expires_at)
        and isinstance(expires_in, int)
        and not isinstance(expires_in, bool)
        and expires_in > 0
    )

    return (
        record.get("provider") == "google"
        and _is_canonical_token_email(record.get("email"))
        and isinstance(access_token, str)
        and bool(access_token.strip())
        and (
            refresh_token is None
            or isinstance(refresh_token, str)
            and bool(refresh_token.strip())
        )
        and (token_type is None or isinstance(token_type, str))
        and (scope is None or isinstance(scope, str))
        and expiry_is_supported
        and _is_supported_token_timestamp(created_at)
        and _is_supported_token_timestamp(updated_at)
        and (
            "credential_generation" not in expected_fields
            or isinstance(credential_generation, str)
            and OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(
                credential_generation
            )
            is not None
        )
    )


def _has_supported_current_google_token_record_shape(record: dict) -> bool:
    fields = frozenset(record)
    if not fields.issubset(CURRENT_GOOGLE_TOKEN_RECORD_FIELDS):
        return False

    access_token = record.get("access_token")
    if "access_token" in record and (
        not isinstance(access_token, str) or not access_token.strip()
    ):
        return False

    refresh_token = record.get("refresh_token")
    if "refresh_token" in record and not (
        refresh_token is None
        or isinstance(refresh_token, str)
        and bool(refresh_token.strip())
    ):
        return False

    for field in ("token_type", "scope"):
        if field in record and record[field] is not None and not isinstance(
            record[field],
            str,
        ):
            return False

    has_expires_at = "expires_at" in record
    has_expires_in = "expires_in" in record
    if has_expires_at != has_expires_in:
        return False
    if has_expires_at:
        expires_at = record["expires_at"]
        expires_in = record["expires_in"]
        if not (
            expires_at is None
            and expires_in is None
            or _is_supported_token_timestamp(expires_at)
            and isinstance(expires_in, int)
            and not isinstance(expires_in, bool)
            and expires_in > 0
        ):
            return False

    for field in ("created_at", "updated_at"):
        if field in record and not _is_supported_token_timestamp(record[field]):
            return False
    credential_generation = record.get("credential_generation")
    if "credential_generation" in record and (
        not isinstance(credential_generation, str)
        or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation) is None
    ):
        return False
    return True


def _is_legacy_owner_equals_mailbox_google_token_record(
    record: dict,
    *,
    normalized_email: str,
    normalized_owner_email: str,
) -> bool:
    existing_owner = record.get("owner_email")
    if not isinstance(existing_owner, str):
        return False

    normalized_existing_owner = normalize_auth_email(existing_owner)
    return (
        _is_canonical_token_email(normalized_email)
        and _is_canonical_token_email(normalized_owner_email)
        and normalized_email != normalized_owner_email
        and normalized_existing_owner == normalized_email
        and _is_canonical_token_email(normalized_existing_owner)
        and record.get("email") == normalized_email
        and (
            _has_supported_google_token_record_shape(
                record,
                PRE_GENERATION_GOOGLE_TOKEN_RECORD_FIELDS,
            )
            or _has_supported_google_token_record_shape(
                record,
                CURRENT_GOOGLE_TOKEN_RECORD_FIELDS,
            )
        )
    )


def _classify_existing_google_token_record(
    existing_record,
    *,
    normalized_email: str,
    normalized_owner_email: str,
) -> str:
    if existing_record is None:
        return GOOGLE_TOKEN_RECORD_ABSENT
    if not isinstance(existing_record, dict):
        return GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS

    existing_provider = existing_record.get("provider")
    existing_email = existing_record.get("email")
    if not isinstance(existing_provider, str) or not isinstance(existing_email, str):
        return GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS
    if existing_provider != "google":
        return GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH
    if not _is_canonical_token_email(existing_email):
        return GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS
    if existing_email != normalized_email:
        return GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH

    if "owner_email" in existing_record:
        existing_owner = existing_record["owner_email"]
        owner_is_canonical = _is_canonical_token_email(existing_owner)
        current_shape_is_supported = (
            _has_supported_google_token_record_shape(
                existing_record,
                PRE_GENERATION_GOOGLE_TOKEN_RECORD_FIELDS,
            )
            or _has_supported_google_token_record_shape(
                existing_record,
                CURRENT_GOOGLE_TOKEN_RECORD_FIELDS,
            )
        )
        if (
            owner_is_canonical
            and current_shape_is_supported
            and existing_owner == normalized_owner_email
        ):
            return GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH
        if _is_legacy_owner_equals_mailbox_google_token_record(
            existing_record,
            normalized_email=normalized_email,
            normalized_owner_email=normalized_owner_email,
        ):
            return GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH
        if not owner_is_canonical or not current_shape_is_supported:
            return GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS
        return GOOGLE_TOKEN_RECORD_OWNER_MISMATCH

    if _has_supported_google_token_record_shape(
        existing_record,
        LEGACY_GOOGLE_TOKEN_RECORD_FIELDS,
    ):
        return GOOGLE_TOKEN_RECORD_LEGACY_OWNERLESS_MATCH
    return GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS


def _resolve_google_token_conflict_diagnostic_code(
    existing_record,
    *,
    record_classification: str,
    normalized_email: str,
) -> str:
    if record_classification == GOOGLE_TOKEN_RECORD_OWNER_MISMATCH:
        if (
            isinstance(existing_record, dict)
            and existing_record.get("owner_email") == normalized_email
        ):
            return "token_legacy_owner_equals_mailbox"
        return "token_owner_mismatch"

    if record_classification == GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH:
        if isinstance(existing_record, dict):
            existing_provider = existing_record.get("provider")
            if isinstance(existing_provider, str) and existing_provider != "google":
                return "token_provider_mismatch"

            existing_email = existing_record.get("email")
            if (
                existing_provider == "google"
                and _is_canonical_token_email(existing_email)
                and existing_email != normalized_email
            ):
                return "token_email_mismatch"
        return "token_owner_conflict"

    if record_classification == GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS:
        if not isinstance(existing_record, dict):
            return "token_record_malformed"

        existing_provider = existing_record.get("provider")
        existing_email = existing_record.get("email")
        existing_owner = existing_record.get("owner_email")
        normalized_email_is_canonical = _is_canonical_token_email(
            normalized_email
        )
        if (
            normalized_email_is_canonical
            and existing_provider == "google"
            and _is_canonical_token_email(existing_email)
            and existing_email == normalized_email
        ):
            if (
                "owner_email" in existing_record
                and isinstance(existing_owner, str)
                and not existing_owner.strip()
                and _has_supported_current_google_token_record_shape(
                    existing_record
                )
            ):
                return "token_owner_fields_empty"

        fields = frozenset(existing_record)
        present_owner_identity_fields = fields & GOOGLE_TOKEN_OWNER_IDENTITY_FIELDS
        # A canonical owner plus exactly one provider/mailbox identity component
        # is a provable partial subset of the current owner identity tuple.
        if (
            normalized_email_is_canonical
            and "owner_email" in existing_record
            and _is_canonical_token_email(existing_owner)
            and len(present_owner_identity_fields) == 2
            and "access_token" in existing_record
            and fields.issubset(CURRENT_GOOGLE_TOKEN_RECORD_FIELDS)
            and (
                "provider" not in existing_record
                or existing_provider == "google"
            )
            and (
                "email" not in existing_record
                or _is_canonical_token_email(existing_email)
                and existing_email == normalized_email
            )
            and _has_supported_current_google_token_record_shape(
                existing_record
            )
        ):
            return "token_owner_fields_partial"

        return "token_record_malformed"

    return "token_owner_conflict"


def build_google_token_record(
    *,
    email: str,
    owner_email: str,
    token_payload: dict,
    existing_record: dict | None = None,
    credential_generation: str | None = None,
) -> dict:
    expires_at, expires_in = _resolve_expiry(token_payload)
    refresh_token = token_payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        refresh_token = (
            existing_record.get("refresh_token")
            if isinstance(existing_record, dict)
            else None
        )

    scope = token_payload.get("scope")
    if (
        not isinstance(scope, str)
        and isinstance(existing_record, dict)
        and isinstance(existing_record.get("scope"), str)
    ):
        scope = existing_record["scope"]
    token_type = token_payload.get("token_type")
    if (
        not isinstance(token_type, str)
        and isinstance(existing_record, dict)
        and isinstance(existing_record.get("token_type"), str)
    ):
        token_type = existing_record["token_type"]
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "provider": "google",
        "email": email,
        "owner_email": owner_email.strip().lower(),
        "access_token": token_payload.get("access_token"),
        "refresh_token": refresh_token,
        "token_type": token_type if isinstance(token_type, str) else None,
        "scope": scope if isinstance(scope, str) else None,
        "expires_at": expires_at,
        "expires_in": expires_in,
        "updated_at": now,
        "created_at": (
            existing_record.get("created_at")
            if isinstance(existing_record, dict)
            and isinstance(existing_record.get("created_at"), str)
            else now
        ),
    }
    if credential_generation is not None:
        if (
            not isinstance(credential_generation, str)
            or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation)
            is None
        ):
            raise ValueError("OAuth credential generation is invalid.")
        record["credential_generation"] = credential_generation
    elif (
        isinstance(existing_record, dict)
        and isinstance(existing_record.get("credential_generation"), str)
        and OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(
            existing_record["credential_generation"]
        )
    ):
        record["credential_generation"] = existing_record["credential_generation"]
    return record


def _perform_rest_request(
    config: dict,
    method: str,
    path: str,
    body: bytes | None = None,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{config['rest_url']}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {config['rest_token']}",
            "Content-Type": "application/json",
        },
        method=method,
    )

    try:
        with urlopen(request, timeout=20) as response:
            raw_payload = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
            if len(raw_payload) > MAX_OAUTH_RESPONSE_BYTES or not raw_payload:
                return None, {
                    "code": "token_persistence_failed",
                    "message": "Durable mailbox storage returned an invalid response.",
                }
            payload = json.loads(raw_payload.decode("utf-8"))
            if not isinstance(payload, dict):
                return None, {
                    "code": "token_persistence_failed",
                    "message": "Durable mailbox storage returned an invalid response.",
                }
            return payload, None
    except HTTPError:
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox storage is temporarily unavailable.",
        }
    except (TimeoutError, URLError, OSError):
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox storage is temporarily unavailable.",
        }
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox storage returned an invalid response.",
        }
    except Exception:
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox storage is temporarily unavailable.",
        }


def _decode_durable_record_payload(
    payload: dict | None,
) -> tuple[dict | None, str | None, dict | None]:
    unreadable_error = {
        "code": "token_persistence_failed",
        "message": "Durable mailbox token storage returned an unreadable token record.",
    }
    if not isinstance(payload, dict) or "result" not in payload:
        return None, None, unreadable_error

    result = payload.get("result")
    if result is None:
        return None, None, None
    if isinstance(result, str) and result.startswith(
        GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX
    ):
        raw_result = result[len(GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX):]
        try:
            parsed = json.loads(raw_result)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None, None, unreadable_error
        if not isinstance(parsed, dict):
            return None, None, unreadable_error
        return parsed, raw_result, None
    return None, None, unreadable_error


def _read_durable_record_snapshot(
    config: dict,
    store_key: str,
) -> tuple[dict | None, str | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "POST",
        "",
        json.dumps(
            ["EVAL", GOOGLE_TOKEN_READ_EXACT_SCRIPT, 1, store_key],
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    if error:
        return None, None, error
    return _decode_durable_record_payload(payload)


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    record, _raw_value, error = _read_durable_record_snapshot(config, store_key)
    return record, error


def _google_token_records_are_type_exact(left, right) -> bool:
    try:
        return json.dumps(
            left,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ) == json.dumps(
            right,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return False


def _google_token_owner_conflict_error() -> dict:
    return {
        "code": "token_owner_conflict",
        "message": "This Google mailbox is already linked to another account owner.",
        GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_owner_conflict",
    }


def _google_token_write_verification_error() -> dict:
    return {
        "code": "token_persistence_failed",
        "message": "Durable mailbox token storage did not confirm the write.",
        GMAIL_CALLBACK_FAILURE_CODE_FIELD: "mailbox_readback_verification_failed",
    }


def _write_conditional_durable_record(
    config: dict,
    store_key: str,
    expected_record: dict | None,
    next_record: dict,
    *,
    mutation_script: str,
) -> tuple[dict | None, dict | None]:
    snapshot, expected_value, snapshot_error = _read_durable_record_snapshot(
        config,
        store_key,
    )
    if snapshot_error:
        return None, snapshot_error

    if expected_record is None:
        if snapshot is not None or expected_value is not None:
            return None, _google_token_owner_conflict_error()
    elif (
        not _google_token_records_are_type_exact(snapshot, expected_record)
        or not isinstance(expected_value, str)
    ):
        return None, _google_token_owner_conflict_error()

    next_value = json.dumps(
        next_record,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    command = [
        "EVAL",
        mutation_script,
        1,
        store_key,
    ]
    if expected_record is not None:
        command.append(expected_value)
    command.append(next_value)

    payload, write_error = _perform_rest_request(
        config,
        "POST",
        "",
        json.dumps(command, separators=(",", ":")).encode("utf-8"),
    )
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, int) and not isinstance(result, bool) and result == 0:
        return None, _google_token_owner_conflict_error()
    acknowledged = (
        isinstance(result, int)
        and not isinstance(result, bool)
        and result == 1
        and write_error is None
    )

    # A transport failure or malformed acknowledgement can mean the atomic
    # mutation committed before the response was lost. Exact readback is the
    # sole authority in that case, and also verifies every acknowledged write.
    verified_record, verify_error = _read_durable_record(config, store_key)
    if verify_error:
        return None, _google_token_write_verification_error()
    if _google_token_records_are_type_exact(verified_record, next_record):
        return verified_record, None
    if (
        verified_record is not None
        and not _google_token_records_are_type_exact(
            verified_record,
            expected_record,
        )
    ):
        return None, _google_token_owner_conflict_error()
    if acknowledged or write_error is not None or result is not None:
        return None, _google_token_write_verification_error()
    return None, _google_token_write_verification_error()


def _write_durable_record(
    config: dict,
    store_key: str,
    expected_record: dict | None,
    next_record: dict,
) -> tuple[dict | None, dict | None]:
    return _write_conditional_durable_record(
        config,
        store_key,
        expected_record,
        next_record,
        mutation_script=(
            GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT
            if expected_record is None
            else GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT
        ),
    )


def _adopt_legacy_durable_record(
    config: dict,
    store_key: str,
    legacy_record: dict,
    next_record: dict,
) -> tuple[dict | None, dict | None]:
    return _write_conditional_durable_record(
        config,
        store_key,
        legacy_record,
        next_record,
        mutation_script=LEGACY_GOOGLE_TOKEN_ADOPTION_SCRIPT,
    )


def _persist_runtime_record(store_key: str, record: dict) -> tuple[dict | None, dict | None]:
    store_path = _resolve_runtime_store_path()
    store = _read_runtime_store(store_path)
    store[store_key] = record

    try:
        _write_runtime_store(store_path, store)
    except OSError:
        return None, {
            "code": "token_persistence_failed",
            "message": "Google authentication succeeded, but mailbox token storage failed.",
        }

    persisted_store = _read_runtime_store(store_path)
    persisted_record = persisted_store.get(store_key)
    return persisted_record if isinstance(persisted_record, dict) else None, None


def persist_google_token_record(
    *,
    email: str,
    owner_email: str,
    token_payload: dict,
    mode: str = "initial",
    credential_generation: str | None = None,
) -> tuple[dict | None, dict | None]:
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None, {
            "code": "invalid_token_payload",
            "message": "Google returned an incomplete token response.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_payload_invalid",
        }
    if mode not in {"initial", "reconnect"} or (
        credential_generation is not None
        and (
            not isinstance(credential_generation, str)
            or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation)
            is None
        )
    ) or (
        mode == "reconnect"
        and (
            not isinstance(credential_generation, str)
            or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation)
            is None
        )
    ):
        return None, {
            "code": "invalid_token_payload",
            "message": "Google returned an incomplete token response.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_payload_invalid",
        }

    normalized_email = email.strip().lower()
    normalized_owner_email = owner_email.strip().lower()
    if not EMAIL_PATTERN.match(normalized_owner_email):
        return None, {
            "code": "invalid_token_owner",
            "message": "Authenticated Gmail token ownership is required.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_payload_invalid",
        }
    store_key = _build_store_key(normalized_email)
    durable_config = _resolve_durable_store_config()
    existing_record = None

    if durable_config:
        existing_record, existing_error = _read_durable_record(durable_config, store_key)
        if existing_error:
            return None, {
                **existing_error,
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_store_unavailable",
            }
    else:
        existing_store = _read_runtime_store(_resolve_runtime_store_path())
        existing_record = existing_store.get(store_key)

    record_classification = _classify_existing_google_token_record(
        existing_record,
        normalized_email=normalized_email,
        normalized_owner_email=normalized_owner_email,
    )
    if record_classification in {
        GOOGLE_TOKEN_RECORD_LEGACY_OWNERLESS_MATCH,
        GOOGLE_TOKEN_RECORD_OWNER_MISMATCH,
        GOOGLE_TOKEN_RECORD_PROVIDER_OR_EMAIL_MISMATCH,
        GOOGLE_TOKEN_RECORD_MALFORMED_OR_AMBIGUOUS,
    }:
        diagnostic_code = _resolve_google_token_conflict_diagnostic_code(
            existing_record,
            record_classification=record_classification,
            normalized_email=normalized_email,
        )
        return None, {
            "code": "token_owner_conflict",
            "message": "This Google mailbox is already linked to another account owner.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: diagnostic_code,
        }

    if (
        record_classification in ADOPTABLE_LEGACY_GOOGLE_TOKEN_RECORD_MATCHES
        and durable_config is None
    ):
        return None, {
            "code": "token_owner_conflict",
            "message": "This Google mailbox is already linked to another account owner.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: (
                "token_legacy_owner_equals_mailbox"
                if record_classification
                == GOOGLE_TOKEN_RECORD_LEGACY_OWNER_EQUALS_MAILBOX_MATCH
                else "token_owner_conflict"
            ),
        }

    response_refresh_token = token_payload.get("refresh_token")
    has_response_refresh_token = (
        isinstance(response_refresh_token, str)
        and bool(response_refresh_token.strip())
    )
    if not has_response_refresh_token:
        existing_refresh_token = (
            existing_record.get("refresh_token")
            if isinstance(existing_record, dict)
            else None
        )
        may_preserve_existing_refresh = (
            mode == "reconnect"
            and record_classification == GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH
            and isinstance(existing_refresh_token, str)
            and bool(existing_refresh_token.strip())
        )
        if not may_preserve_existing_refresh:
            return None, {
                "code": "invalid_token_payload",
                "message": "Google did not return a durable refresh authorization.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "refresh_token_missing",
            }

    if record_classification in ADOPTABLE_LEGACY_GOOGLE_TOKEN_RECORD_MATCHES:
        refresh_token = token_payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            return None, {
                "code": "invalid_token_payload",
                "message": "Google returned an incomplete token response.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_payload_invalid",
            }

    next_record = None
    persisted_record = None
    error = None
    storage_backend = (
        durable_config["backend"] if durable_config else "runtime_tmp_file"
    )
    storage_durable = durable_config is not None
    for write_attempt in range(MAX_GMAIL_TOKEN_WRITE_ATTEMPTS):
        try:
            next_record = build_google_token_record(
                email=normalized_email,
                owner_email=normalized_owner_email,
                token_payload=token_payload,
                existing_record=(
                    existing_record
                    if record_classification
                    == GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH
                    else None
                ),
                credential_generation=credential_generation,
            )
        except ValueError:
            return None, {
                "code": "invalid_token_payload",
                "message": "Google returned an incomplete token response.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "token_payload_invalid",
            }

        if durable_config:
            if record_classification in ADOPTABLE_LEGACY_GOOGLE_TOKEN_RECORD_MATCHES:
                persisted_record, error = _adopt_legacy_durable_record(
                    durable_config,
                    store_key,
                    existing_record,
                    next_record,
                )
            else:
                persisted_record, error = _write_durable_record(
                    durable_config,
                    store_key,
                    existing_record,
                    next_record,
                )
        else:
            persisted_record, error = _persist_runtime_record(
                store_key,
                next_record,
            )

        if not error:
            break
        if (
            not durable_config
            or mode != "reconnect"
            or error.get("code") != "token_owner_conflict"
            or record_classification != GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH
            or write_attempt + 1 >= MAX_GMAIL_TOKEN_WRITE_ATTEMPTS
        ):
            break

        # A refresh that began before this callback may win the first CAS. It
        # is safe to retry only when the winner is still the exact same owner
        # and credential generation. A different generation is a newer
        # reconnect and must win.
        previous_generation = existing_record.get("credential_generation")
        raced_record, raced_error = _read_durable_record(
            durable_config,
            store_key,
        )
        if raced_error:
            error = raced_error
            break
        raced_classification = _classify_existing_google_token_record(
            raced_record,
            normalized_email=normalized_email,
            normalized_owner_email=normalized_owner_email,
        )
        raced_refresh_token = (
            raced_record.get("refresh_token")
            if isinstance(raced_record, dict)
            else None
        )
        raced_generation = (
            raced_record.get("credential_generation")
            if isinstance(raced_record, dict)
            else None
        )
        if (
            raced_classification != GOOGLE_TOKEN_RECORD_EXACT_OWNER_MATCH
            or not isinstance(raced_refresh_token, str)
            or not raced_refresh_token.strip()
        ):
            break
        if raced_generation == credential_generation:
            # Our CAS committed, then a refresh derived a newer access token
            # from that exact reconnect generation before readback.
            persisted_record = raced_record
            next_record = raced_record
            error = None
            break
        if raced_generation != previous_generation:
            break
        existing_record = raced_record
        record_classification = raced_classification

    if error:
        diagnostic_code = _resolve_gmail_callback_failure_code(
            error,
            default="token_persistence_failed",
        )
        return None, {
            **error,
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: diagnostic_code,
        }

    if not isinstance(next_record, dict) or not isinstance(persisted_record, dict):
        return None, {
            "code": "token_persistence_failed",
            "message": "Google authentication succeeded, but mailbox token storage could not be verified.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "mailbox_readback_verification_failed",
        }

    if (
        not _google_token_records_are_type_exact(persisted_record, next_record)
        or persisted_record.get("provider") != "google"
        or persisted_record.get("email") != normalized_email
        or persisted_record.get("owner_email") != normalized_owner_email
    ):
        return None, {
            "code": "token_persistence_failed",
            "message": "Google authentication succeeded, but the stored mailbox token record is incomplete.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "mailbox_readback_verification_failed",
        }

    return {
        **persisted_record,
        "_storage_backend": storage_backend,
        "_storage_durable": storage_durable,
    }, None


def _resolve_authenticated_member_request(request: BaseHTTPRequestHandler):
    try:
        raw_headers = http.snapshot_request_headers(request)
        resolution = runtime.resolve_authenticated_member(raw_headers)
        if (
            resolution.outcome is runtime.MemberResolutionOutcome.AUTHENTICATED
            and resolution.member is not None
        ):
            return resolution.member, resolution.set_cookies
        return None, resolution.set_cookies
    except Exception:
        return None, _MEMBER_AUTHORITY_UNAVAILABLE


def _resolve_current_gmail_callback_member(
    request: BaseHTTPRequestHandler,
    *,
    state_payload: dict,
    state_signing_secret: str,
) -> tuple[runtime.AuthenticatedMemberContext | None, str | None]:
    member, auth_set_cookies = _resolve_authenticated_member_request(request)
    if member is None:
        return None, (
            "member_authority_unavailable"
            if auth_set_cookies is _MEMBER_AUTHORITY_UNAVAILABLE
            else "member_unauthenticated"
        )
    if not verify_owner_binding(
        state_payload,
        member.email,
        state_signing_secret,
        member_user_id=member.user_id,
        member_workspace_id=member.workspace_id,
    ):
        return None, "owner_binding_invalid"
    return member, None


def _format_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    if not local_part:
        return email

    return " ".join(part.capitalize() for part in local_part.split())


def _build_gmail_managed_inbox_id(email: str, existing_ids: set[str]) -> str:
    normalized_existing_ids = {
        existing_id.casefold()
        for existing_id in existing_ids
        if isinstance(existing_id, str)
    }
    local_part = email.split("@", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local_part).strip("-") or "gmail"
    candidate = f"gmail-{slug}"
    if candidate.casefold() not in normalized_existing_ids:
        return candidate

    domain_slug = re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-") or "gmail"
    candidate = f"gmail-{domain_slug}"
    suffix = 2
    while candidate.casefold() in normalized_existing_ids:
        candidate = f"gmail-{domain_slug}-{suffix}"
        suffix += 1

    return candidate


def _create_empty_managed_imap_settings() -> dict:
    return {
        "host": "",
        "port": "",
        "ssl": True,
        "username": "",
        "password": "",
    }


def _create_empty_managed_smtp_settings() -> dict:
    return {
        "host": "",
        "port": "",
        "security": "starttls",
        "username": "",
        "password": "",
        "useSameCredentials": True,
    }


def _gmail_link_conflict(message: str) -> dict:
    return {"code": "gmail_link_conflict", "message": message}


def _gmail_reconnect_error(
    code: str,
    message: str,
    failure_code: str,
) -> dict:
    return {
        "code": code,
        "message": message,
        GMAIL_CALLBACK_FAILURE_CODE_FIELD: failure_code,
    }


def _prepare_gmail_reconnect_target(
    member: runtime.AuthenticatedMemberContext,
    *,
    mailbox_id: str,
    expected_email: str,
    credential_generation: str,
) -> tuple[dict | None, dict | None]:
    """Resolve one exact generation-reserved Gmail target owned by this member."""
    normalized_owner = normalize_auth_email(member.email)
    if (
        not isinstance(mailbox_id, str)
        or OAUTH_MAILBOX_ID_PATTERN.fullmatch(mailbox_id) is None
        or not isinstance(expected_email, str)
        or expected_email != expected_email.strip().lower()
        or EMAIL_PATTERN.fullmatch(expected_email) is None
        or not isinstance(credential_generation, str)
        or OAUTH_CREDENTIAL_GENERATION_PATTERN.fullmatch(credential_generation)
        is None
    ):
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "The selected Google mailbox reconnect target is invalid.",
            "oauth_reconnect_target_invalid",
        )

    durable_config = _resolve_durable_store_config()
    if not durable_config:
        return None, _gmail_reconnect_error(
            "user_config_store_unavailable",
            "User config storage is unavailable.",
            "user_config_store_unavailable",
        )
    read_result = user_config_store.read_user_config_record(
        durable_config,
        member.email,
    )
    if not isinstance(read_result, dict) or read_result.get("status") == "unavailable":
        return None, _gmail_reconnect_error(
            "user_config_store_unavailable",
            "User config storage is temporarily unavailable.",
            "user_config_store_unavailable",
        )
    config = read_result.get("config")
    if read_result.get("status") != "ok" or not isinstance(config, dict):
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "The selected Google mailbox no longer exists.",
            "oauth_reconnect_stale",
        )

    stored_config_owner = config.get("email")
    managed_inboxes = config.get("managedInboxes")
    if (
        not isinstance(stored_config_owner, str)
        or normalize_auth_email(stored_config_owner) != normalized_owner
        or not isinstance(managed_inboxes, list)
        or any(not isinstance(inbox, dict) for inbox in managed_inboxes)
    ):
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "The selected Google mailbox reconnect target is invalid.",
            "oauth_reconnect_target_invalid",
        )

    exact_matches = [
        (index, inbox)
        for index, inbox in enumerate(managed_inboxes)
        if inbox.get("id") == mailbox_id
    ]
    casefold_matches = [
        inbox
        for inbox in managed_inboxes
        if isinstance(inbox.get("id"), str)
        and inbox["id"].casefold() == mailbox_id.casefold()
    ]
    if len(exact_matches) != 1 or len(casefold_matches) != 1:
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "The selected Google mailbox no longer exists.",
            "oauth_reconnect_stale",
        )

    target_index, target = exact_matches[0]
    raw_target_email = target.get("email")
    target_email = (
        normalize_auth_email(raw_target_email)
        if isinstance(raw_target_email, str)
        else ""
    )
    stored_target_owner = target.get("oauthOwnerEmail")
    email_matches = [
        inbox
        for inbox in managed_inboxes
        if isinstance(inbox.get("email"), str)
        and normalize_auth_email(inbox["email"]) == expected_email
    ]
    if (
        target.get("provider") != "google"
        or target_email != expected_email
        or len(email_matches) != 1
        or (
            stored_target_owner is not None
            and (
                not isinstance(stored_target_owner, str)
                or normalize_auth_email(stored_target_owner) != normalized_owner
            )
        )
    ):
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "The selected Google mailbox changed before reconnect completed.",
            "oauth_reconnect_stale",
        )
    if target.get("oauthReconnectGeneration") != credential_generation:
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            "A newer reconnect attempt replaced this one. Please try again.",
            "oauth_reconnect_stale",
        )

    return {
        "durable_config": durable_config,
        "existing_config": config,
        "target_index": target_index,
        "target": deepcopy(target),
    }, None


def _register_gmail_reconnect_in_user_config(
    member: runtime.AuthenticatedMemberContext,
    *,
    mailbox_id: str,
    expected_email: str,
    verified_email: str,
    owner_email: str,
    message: str,
    credential_generation: str,
) -> tuple[dict | None, dict | None]:
    if (
        normalize_auth_email(member.email) != normalize_auth_email(owner_email)
        or normalize_auth_email(verified_email) != expected_email
    ):
        return None, _gmail_reconnect_error(
            "gmail_link_conflict",
            f"Please reconnect using the Google account for {expected_email}.",
            "oauth_reconnect_email_mismatch",
        )

    for _attempt in range(MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS):
        preparation, preparation_error = _prepare_gmail_reconnect_target(
            member,
            mailbox_id=mailbox_id,
            expected_email=expected_email,
            credential_generation=credential_generation,
        )
        if preparation_error or not preparation:
            return None, preparation_error

        existing_config = preparation["existing_config"]
        target_index = preparation["target_index"]
        next_record = deepcopy(existing_config)
        next_target = deepcopy(preparation["target"])
        next_target.update(
            {
                "id": mailbox_id,
                "email": expected_email,
                "provider": "google",
                "oauthOwnerEmail": normalize_auth_email(owner_email),
                "connected": True,
                "connectionMethod": "oauth",
                "connectionType": "oauth",
                "connectionStatus": "connected",
                "connectionMessage": message,
                "oauthAuthorizationUrl": None,
            }
        )
        next_target.pop("oauthReconnectGeneration", None)
        next_record["managedInboxes"][target_index] = next_target
        next_record["updatedAt"] = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )

        write_result = user_config_store.write_user_config_record_if_unchanged(
            preparation["durable_config"],
            member.email,
            existing_config,
            next_record,
        )
        if not isinstance(write_result, dict):
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage did not confirm the reconnect.",
                "user_config_write_failed",
            )
        if write_result.get("status") in {"conflict", "missing"}:
            continue
        if write_result.get("status") != "ok":
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage did not confirm the reconnect.",
                "user_config_write_failed",
            )
        written_record = write_result.get("record")
        if not _user_config_records_are_type_exact(
            written_record,
            next_record,
        ):
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage did not confirm the intended reconnect settings.",
                "user_config_write_failed",
            )

        readback = user_config_store.read_user_config_record(
            preparation["durable_config"],
            member.email,
        )
        verified_config = (
            readback.get("config")
            if isinstance(readback, dict) and readback.get("status") == "ok"
            else None
        )
        if not isinstance(verified_config, dict) or not _user_config_records_are_type_exact(
            verified_config,
            next_record,
        ):
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage could not verify the reconnected mailbox.",
                "user_config_readback_failed",
            )
        verified_inboxes = verified_config.get("managedInboxes")
        matches = (
            [
                inbox
                for inbox in verified_inboxes
                if isinstance(inbox, dict) and inbox.get("id") == mailbox_id
            ]
            if isinstance(verified_inboxes, list)
            else []
        )
        if len(matches) != 1:
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage could not verify the reconnected mailbox.",
                "mailbox_readback_verification_failed",
            )
        saved = matches[0]
        if (
            saved.get("email") != expected_email
            or saved.get("provider") != "google"
            or saved.get("connected") is not True
            or saved.get("connectionStatus") != "connected"
            or saved.get("oauthReconnectGeneration") is not None
        ):
            return None, _gmail_reconnect_error(
                "user_config_persistence_failed",
                "User config storage could not verify the reconnected mailbox.",
                "mailbox_readback_verification_failed",
            )
        return deepcopy(saved), None

    return None, _gmail_reconnect_error(
        "gmail_link_conflict",
        "The mailbox changed before reconnect could be committed.",
        "oauth_reconnect_stale",
    )


def _resolve_gmail_managed_inbox_target(
    managed_inboxes: list,
    *,
    email: str,
    owner_email: str,
    inbox_position: str | None,
) -> tuple[int | None, dict | None]:
    normalized_email = email.strip().lower()
    normalized_owner_email = normalize_auth_email(owner_email)
    if (
        not EMAIL_PATTERN.match(normalized_email)
        or not EMAIL_PATTERN.match(normalized_owner_email)
    ):
        return None, _gmail_link_conflict("Verified Google mailbox identity is invalid.")
    if inbox_position is not None and (
        inbox_position not in ONBOARDING_PRESET_INBOX_IDS
        and ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(inbox_position) is None
    ):
        return None, _gmail_link_conflict("Onboarding inbox position is invalid.")

    normalized_ids: set[str] = set()
    email_matches: list[int] = []
    position_matches: list[int] = []
    for index, mailbox in enumerate(managed_inboxes):
        if not isinstance(mailbox, dict):
            return None, _gmail_link_conflict(
                "Existing managed inbox configuration is ambiguous."
            )

        mailbox_id = mailbox.get("id")
        if (
            not isinstance(mailbox_id, str)
            or not mailbox_id.strip()
            or mailbox_id != mailbox_id.strip()
        ):
            return None, _gmail_link_conflict(
                "Existing managed inbox configuration is ambiguous."
            )
        normalized_id = mailbox_id.casefold()
        if normalized_id in normalized_ids:
            return None, _gmail_link_conflict(
                "Existing managed inbox configuration is ambiguous."
            )
        normalized_ids.add(normalized_id)

        stored_position = mailbox.get("onboardingInboxId")
        if stored_position is not None and (
            not isinstance(stored_position, str)
            or (
                stored_position not in ONBOARDING_PRESET_INBOX_IDS
                and ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(stored_position)
                is None
            )
        ):
            return None, _gmail_link_conflict(
                "Existing managed inbox configuration is ambiguous."
            )

        mailbox_email = mailbox.get("email")
        if (
            isinstance(mailbox_email, str)
            and mailbox_email.strip().lower() == normalized_email
        ):
            email_matches.append(index)
        if inbox_position is not None and mailbox.get("onboardingInboxId") == inbox_position:
            position_matches.append(index)

    if len(email_matches) > 1 or len(position_matches) > 1:
        return None, _gmail_link_conflict(
            "Existing Gmail mailbox registration is ambiguous."
        )

    email_match = email_matches[0] if email_matches else None
    position_match = position_matches[0] if position_matches else None
    if email_match is not None:
        matched_mailbox = managed_inboxes[email_match]
        if matched_mailbox.get("provider") not in ("google", "gmail", None):
            return None, _gmail_link_conflict(
                "This mailbox already uses a different connection provider."
            )
        existing_position = matched_mailbox.get("onboardingInboxId")
        if (
            inbox_position is not None
            and existing_position is not None
            and existing_position != inbox_position
        ):
            return None, _gmail_link_conflict(
                "This Google mailbox is already linked to another onboarding inbox."
            )

    if position_match is not None:
        matched_mailbox = managed_inboxes[position_match]
        mailbox_email = matched_mailbox.get("email")
        if (
            not isinstance(mailbox_email, str)
            or mailbox_email.strip().lower() != normalized_email
        ):
            return None, _gmail_link_conflict(
                "This onboarding inbox is already linked to another mailbox."
            )
        if matched_mailbox.get("provider") not in ("google", "gmail", None):
            return None, _gmail_link_conflict(
                "This onboarding inbox already uses a different connection provider."
            )

    if (
        email_match is not None
        and position_match is not None
        and email_match != position_match
    ):
        return None, _gmail_link_conflict(
            "Existing Gmail mailbox registration is ambiguous."
        )

    matched_index = email_match if email_match is not None else position_match
    if matched_index is not None:
        matched_mailbox = managed_inboxes[matched_index]
        stored_owner = matched_mailbox.get("oauthOwnerEmail")
        if stored_owner is not None and (
            not isinstance(stored_owner, str)
            or normalize_auth_email(stored_owner) != normalized_owner_email
        ):
            return None, _gmail_link_conflict(
                "This Google mailbox belongs to another authenticated owner."
            )

    return matched_index, None


def _upsert_gmail_managed_inbox_record(
    managed_inboxes: list,
    *,
    email: str,
    display_name: str | None,
    owner_email: str,
    message: str,
    inbox_position: str | None = None,
) -> list:
    normalized_email = email.strip().lower()
    if not EMAIL_PATTERN.match(normalized_email):
        return managed_inboxes

    next_inboxes = [
        dict(mailbox) if isinstance(mailbox, dict) else mailbox
        for mailbox in managed_inboxes
    ]
    existing_ids = {
        mailbox.get("id", "").strip()
        for mailbox in next_inboxes
        if isinstance(mailbox, dict) and isinstance(mailbox.get("id"), str)
    }
    matched_index = None
    for index, mailbox in enumerate(next_inboxes):
        if not isinstance(mailbox, dict):
            continue

        mailbox_email = mailbox.get("email")
        mailbox_provider = mailbox.get("provider")
        if (
            isinstance(mailbox_email, str)
            and mailbox_email.strip().lower() == normalized_email
            and mailbox_provider in ("google", "gmail", None)
        ):
            matched_index = index
            break

    requested_title = (display_name or "").strip()
    title = (
        requested_title
        if requested_title
        and len(requested_title) <= 160
        and not any(ord(character) < 32 or ord(character) == 127 for character in requested_title)
        else _format_name_from_email(normalized_email)
    )
    safe_defaults = {
        "title": title,
        "email": normalized_email,
        "provider": "google",
        "oauthOwnerEmail": normalize_auth_email(owner_email),
        "connected": True,
        "connectionMethod": "oauth",
        "connectionType": "oauth",
        "connectionStatus": "connected",
        "connectionMessage": message,
        "oauthAuthorizationUrl": None,
        "customImap": _create_empty_managed_imap_settings(),
        "customSmtp": _create_empty_managed_smtp_settings(),
    }
    if inbox_position is not None:
        safe_defaults["onboardingInboxId"] = inbox_position

    if matched_index is None:
        next_inboxes.append(
            {
                "id": _build_gmail_managed_inbox_id(normalized_email, existing_ids),
                **safe_defaults,
            }
        )
        return next_inboxes

    existing_mailbox = next_inboxes[matched_index]
    existing_id = existing_mailbox.get("id") if isinstance(existing_mailbox, dict) else None
    next_inboxes[matched_index] = {
        **existing_mailbox,
        **safe_defaults,
        "id": (
            existing_id.strip()
            if isinstance(existing_id, str) and existing_id.strip()
            else _build_gmail_managed_inbox_id(normalized_email, existing_ids)
        ),
        "title": (
            existing_mailbox.get("title")
            if isinstance(existing_mailbox.get("title"), str)
            and existing_mailbox.get("title").strip()
            else title
        ),
    }
    return next_inboxes


def _verify_saved_gmail_mailbox(
    record: dict,
    intended_mailbox: dict,
    owner_email: str,
    expected_updated_at: str,
) -> bool:
    managed_inboxes = record.get("managedInboxes")
    if not isinstance(managed_inboxes, list):
        return False
    intended_id = intended_mailbox.get("id")
    matches = [
        mailbox
        for mailbox in managed_inboxes
        if isinstance(mailbox, dict) and mailbox.get("id") == intended_id
    ]
    if len(matches) != 1:
        return False
    saved = matches[0]
    return (
        saved.get("id") == intended_id
        and saved.get("email") == intended_mailbox.get("email")
        and saved.get("provider") == "google"
        and saved.get("connected") is True
        and saved.get("connectionStatus") == "connected"
        and saved.get("oauthOwnerEmail") == normalize_auth_email(owner_email)
        and saved.get("onboardingInboxId")
        == intended_mailbox.get("onboardingInboxId")
        and normalize_auth_email(str(record.get("email") or ""))
        == normalize_auth_email(owner_email)
        and record.get("updatedAt") == expected_updated_at
    )


def _user_config_records_are_type_exact(left, right) -> bool:
    try:
        return json.dumps(
            left,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ) == json.dumps(
            right,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return False


def _validate_current_gmail_onboarding_authority(
    existing_config: dict,
    *,
    inbox_position: str | None,
) -> dict | None:
    if inbox_position is None:
        return None

    normalized_session = existing_config.get("onboardingSession")
    if (
        not isinstance(normalized_session, dict)
        or normalized_session.get("schemaVersion") != 1
        or normalized_session.get("completed") is not False
    ):
        return {
            **_gmail_link_conflict(
                "The selected onboarding flow is no longer active."
            ),
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "gmail_link_conflict",
        }

    choices = normalized_session.get("choices")
    selected_inboxes = (
        choices.get("selectedInboxes") if isinstance(choices, dict) else None
    )
    if (
        not isinstance(selected_inboxes, list)
        or selected_inboxes.count(inbox_position) != 1
    ):
        return {
            **_gmail_link_conflict(
                "The selected onboarding inbox is no longer available."
            ),
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "gmail_link_conflict",
        }
    return None


def _prepare_gmail_managed_inbox_registration(
    member: runtime.AuthenticatedMemberContext,
    *,
    email: str,
    owner_email: str,
    inbox_position: str | None,
) -> tuple[dict | None, dict | None]:
    if (
        normalize_auth_email(member.email) != normalize_auth_email(owner_email)
    ):
        return None, {
            "code": "unauthorized",
            "message": "OAuth session ownership could not be verified.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "owner_binding_invalid",
        }
    durable_config = _resolve_durable_store_config()
    if not durable_config:
        return None, {
            "code": "user_config_store_unavailable",
            "message": "User config storage is unavailable.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_store_unavailable",
        }

    read_result = user_config_store.read_user_config_record(
        durable_config,
        member.email,
    )
    if not isinstance(read_result, dict):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage is temporarily unavailable.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_preflight_failed",
        }

    read_status = read_result.get("status")
    read_config = read_result.get("config")
    if read_status == "missing" and read_config is None:
        existing_config = {}
    elif read_status == "ok" and isinstance(read_config, dict):
        existing_config = read_config
    elif read_status == "unavailable":
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage is temporarily unavailable.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_preflight_failed",
        }
    else:
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage returned an invalid record.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_invalid",
        }

    stored_owner = existing_config.get("email")
    if stored_owner is not None and (
        not isinstance(stored_owner, str)
        or normalize_auth_email(stored_owner) != normalize_auth_email(member.email)
    ):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config ownership could not be verified.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_invalid",
        }

    onboarding_authority_error = _validate_current_gmail_onboarding_authority(
        existing_config,
        inbox_position=inbox_position,
    )
    if onboarding_authority_error:
        return None, onboarding_authority_error

    existing_managed_inboxes = existing_config.get("managedInboxes")
    if "managedInboxes" not in existing_config:
        existing_managed_inboxes = []
    elif not isinstance(existing_managed_inboxes, list):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "Existing managed inbox configuration is malformed.",
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_invalid",
        }

    _, conflict_error = _resolve_gmail_managed_inbox_target(
        existing_managed_inboxes,
        email=email,
        owner_email=owner_email,
        inbox_position=inbox_position,
    )
    if conflict_error:
        return None, {
            **conflict_error,
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "gmail_link_conflict",
        }

    return {
        "durable_config": durable_config,
        "read_status": read_status,
        "existing_config": existing_config,
        "existing_managed_inboxes": existing_managed_inboxes,
    }, None


def _build_gmail_user_config_mutation(
    preparation: dict,
    *,
    member_email: str,
    email: str,
    display_name: str | None,
    owner_email: str,
    message: str,
    inbox_position: str | None,
) -> tuple[dict | None, dict | None]:
    existing_config = preparation["existing_config"]
    existing_managed_inboxes = preparation["existing_managed_inboxes"]

    next_record = {
        "v": USER_CONFIG_SCHEMA_VERSION,
        "email": normalize_auth_email(member_email),
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "onboardingSession": {},
        "managedInboxes": [],
        "mailboxTitleOverrides": {},
        "primaryManagedInboxId": None,
        "mailboxFocusPreferenceOverrides": {},
        "inboxSignatures": {},
        "smartFolders": [],
        "uiPreferences": {},
        "displayNameOverrides": {},
        **existing_config,
    }
    next_record["v"] = USER_CONFIG_SCHEMA_VERSION
    next_record["email"] = normalize_auth_email(member_email)
    next_record["updatedAt"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    next_record["managedInboxes"] = _upsert_gmail_managed_inbox_record(
        existing_managed_inboxes,
        email=email,
        display_name=display_name,
        owner_email=owner_email,
        message=message,
        inbox_position=inbox_position,
    )

    intended_mailboxes = [
        mailbox
        for mailbox in next_record["managedInboxes"]
        if isinstance(mailbox, dict)
        and mailbox.get("provider") == "google"
        and mailbox.get("email") == email.strip().lower()
        and mailbox.get("oauthOwnerEmail") == normalize_auth_email(owner_email)
        and (
            inbox_position is None
            or mailbox.get("onboardingInboxId") == inbox_position
        )
    ]
    if len(intended_mailboxes) != 1:
        return None, {
            "code": "user_config_persistence_failed",
            "message": (
                "User config storage could not prepare the verified Gmail mailbox."
            ),
            GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_invalid",
        }
    return {
        "next_record": next_record,
        "intended_mailbox": intended_mailboxes[0],
    }, None


def _register_gmail_managed_inbox_in_user_config(
    member: runtime.AuthenticatedMemberContext,
    *,
    email: str,
    display_name: str | None,
    owner_email: str,
    message: str,
    inbox_position: str | None,
) -> tuple[dict | None, dict | None]:
    for _attempt in range(MAX_GMAIL_USER_CONFIG_WRITE_ATTEMPTS):
        preparation, preparation_error = (
            _prepare_gmail_managed_inbox_registration(
                member,
                email=email,
                owner_email=owner_email,
                inbox_position=inbox_position,
            )
        )
        if preparation_error or not preparation:
            return None, preparation_error

        mutation, mutation_error = _build_gmail_user_config_mutation(
            preparation,
            member_email=member.email,
            email=email,
            display_name=display_name,
            owner_email=owner_email,
            message=message,
            inbox_position=inbox_position,
        )
        if mutation_error or not mutation:
            return None, mutation_error

        durable_config = preparation["durable_config"]
        existing_config = preparation["existing_config"]
        next_record = mutation["next_record"]
        intended_mailbox = mutation["intended_mailbox"]
        if preparation["read_status"] == "missing":
            write_result = user_config_store.write_user_config_record_if_missing(
                durable_config,
                member.email,
                next_record,
            )
        else:
            write_result = (
                user_config_store.write_user_config_record_if_unchanged(
                    durable_config,
                    member.email,
                    existing_config,
                    next_record,
                )
            )

        if not isinstance(write_result, dict):
            return None, {
                "code": "user_config_persistence_failed",
                "message": "User config storage did not confirm the write.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_write_failed",
            }
        write_status = write_result.get("status")
        if write_status in {"conflict", "missing"}:
            continue
        if write_status != "ok":
            return None, {
                "code": "user_config_persistence_failed",
                "message": "User config storage did not confirm the write.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_write_failed",
            }
        expected_readback = write_result.get("record")
        if not isinstance(expected_readback, dict):
            return None, {
                "code": "user_config_persistence_failed",
                "message": "User config storage did not confirm the write.",
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_write_failed",
            }

        readback_result = user_config_store.read_user_config_record(
            durable_config,
            member.email,
        )
        verified_record = (
            readback_result.get("config")
            if isinstance(readback_result, dict)
            and readback_result.get("status") == "ok"
            else None
        )
        if not isinstance(verified_record, dict):
            return None, {
                "code": "user_config_persistence_failed",
                "message": (
                    "User config storage could not verify the saved mailbox."
                ),
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_readback_failed",
            }
        if not _user_config_records_are_type_exact(
            verified_record,
            expected_readback,
        ):
            return None, {
                "code": "user_config_persistence_failed",
                "message": (
                    "User config storage could not verify the saved config record."
                ),
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: (
                    "mailbox_readback_verification_failed"
                ),
            }
        if not _verify_saved_gmail_mailbox(
            verified_record,
            intended_mailbox,
            owner_email,
            next_record["updatedAt"],
        ):
            return None, {
                "code": "user_config_persistence_failed",
                "message": (
                    "User config storage could not verify the saved Gmail mailbox."
                ),
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: (
                    "mailbox_readback_verification_failed"
                ),
            }
        verified_inboxes = verified_record.get("managedInboxes")
        saved_mailboxes = (
            [
                mailbox
                for mailbox in verified_inboxes
                if isinstance(mailbox, dict)
                and mailbox.get("id") == intended_mailbox.get("id")
            ]
            if isinstance(verified_inboxes, list)
            else []
        )
        if len(saved_mailboxes) != 1:
            return None, {
                "code": "user_config_persistence_failed",
                "message": (
                    "User config storage could not verify the saved Gmail mailbox."
                ),
                GMAIL_CALLBACK_FAILURE_CODE_FIELD: (
                    "mailbox_readback_verification_failed"
                ),
            }
        return dict(saved_mailboxes[0]), None

    return None, {
        "code": "user_config_persistence_failed",
        "message": "User config changed before the Gmail mailbox could be saved.",
        GMAIL_CALLBACK_FAILURE_CODE_FIELD: "user_config_write_failed",
    }


def _upsert_gmail_managed_inbox_in_user_config(
    member: runtime.AuthenticatedMemberContext,
    *,
    email: str,
    display_name: str | None,
    owner_email: str,
    message: str,
    inbox_position: str | None = None,
) -> dict | None:
    _, error = _register_gmail_managed_inbox_in_user_config(
        member,
        email=email,
        display_name=display_name,
        owner_email=owner_email,
        message=message,
        inbox_position=inbox_position,
    )
    return error

OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result"


def _build_app_redirect_url(headers) -> str | None:
    public_origin = resolve_public_app_origin(headers)
    if not public_origin:
        return None
    return f"{public_origin}/"


def _build_callback_payload(
    *,
    provider: str,
    email: str,
    connection_status: str,
    message: str,
    connected: bool,
    display_name: str | None = None,
    inbox_position: str | None = None,
    mailbox_id: str | None = None,
    mode: str | None = None,
) -> dict:
    if provider == "google":
        is_success = (
            connected is True
            and connection_status == "connected"
            and isinstance(mailbox_id, str)
            and bool(mailbox_id.strip())
            and isinstance(email, str)
            and EMAIL_PATTERN.match(email.strip().lower()) is not None
        )
        payload = {
            "status": "success" if is_success else "error",
            "provider": "google",
            "message": message,
        }
        if mode in {"initial", "reconnect"}:
            payload["mode"] = mode
        normalized_email = email.strip().lower() if isinstance(email, str) else ""
        if EMAIL_PATTERN.match(normalized_email):
            payload["email"] = normalized_email
        if isinstance(inbox_position, str) and (
            inbox_position in ONBOARDING_PRESET_INBOX_IDS
            or ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(inbox_position)
            is not None
        ):
            payload["inboxPosition"] = inbox_position
        if is_success or (
            mode == "reconnect"
            and isinstance(mailbox_id, str)
            and OAUTH_MAILBOX_ID_PATTERN.fullmatch(mailbox_id)
        ):
            payload["mailboxId"] = mailbox_id.strip()
        return payload

    payload = {
        "provider": provider,
        "email": email,
        "connectionMethod": "oauth",
        "connectionStatus": connection_status,
        "connected": connected,
        "message": message,
    }
    if display_name:
        payload["displayName"] = display_name

    return payload


def _render_callback_bridge_page(app_redirect_url: str, payload: dict) -> bytes:
    payload_json = json.dumps(payload).replace("</", "<\\/")
    redirect_json = json.dumps(app_redirect_url).replace("</", "<\\/")
    storage_key_json = json.dumps(OAUTH_CALLBACK_RESULT_STORAGE_KEY)
    callback_path_json = json.dumps(OAUTH_CALLBACK_PATH)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="referrer" content="no-referrer" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cuevion Gmail Connection</title>
  </head>
  <body>
    <p id="oauth-status">Returning to Cuevion…</p>
    <p><a id="oauth-return" href="/">Return to Cuevion</a></p>
    <script>
      const payload = {payload_json};
      const redirectUrl = {redirect_json};
      const statusNode = document.getElementById("oauth-status");
      const returnLink = document.getElementById("oauth-return");
      statusNode.textContent = typeof payload.message === "string" && payload.message
        ? payload.message
        : "Returning to Cuevion…";
      returnLink.href = redirectUrl;
      window.history.replaceState(null, "", {callback_path_json});
      try {{
        window.localStorage.setItem({storage_key_json}, JSON.stringify(payload));
      }} catch (_error) {{
        // The visible message and return link remain available if storage is blocked.
      }}
      window.setTimeout(
        () => window.location.replace(redirectUrl),
        payload.status === "error" ? 1500 : 0,
      );
    </script>
  </body>
</html>
"""
    return html.encode("utf-8")


def _render_callback_configuration_error_page() -> bytes:
    return b"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="referrer" content="no-referrer" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cuevion Gmail Connection</title>
  </head>
  <body>
    <script>
      window.history.replaceState(null, "", "/api/inboxes/oauth-callback");
    </script>
    <p>Mailbox authentication could not be completed because the application is not configured safely.</p>
  </body>
</html>
"""


def _exchange_google_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> tuple[dict | None, dict | None]:
    request_payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_TOKEN_ENDPOINT,
        data=request_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
            if len(body) > MAX_OAUTH_RESPONSE_BYTES:
                return None, {"code": "token_exchange_failed", "message": "Google returned an invalid token response."}
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                return None, {"code": "token_exchange_failed", "message": "Google returned an invalid token response."}
            return payload, None
    except HTTPError as error:
        try:
            raw_error_body = error.read(MAX_OAUTH_RESPONSE_BYTES + 1)
        except Exception:
            return None, {
                "code": "token_exchange_unavailable",
                "message": "Google token exchange was unavailable.",
            }
        if len(raw_error_body) > MAX_OAUTH_RESPONSE_BYTES:
            return None, {
                "code": "token_exchange_unavailable",
                "message": "Google token exchange was unavailable.",
            }
        error_body = raw_error_body.decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}
        return None, {
            "code": "token_exchange_failed",
            "message": (
                parsed_error.get("error_description")
                or parsed_error.get("error")
                or "Google token exchange failed."
            ),
        }
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        return None, {
            "code": "token_exchange_unavailable",
            "message": "Google token exchange was unavailable.",
        }


def _fetch_verified_google_identity(access_token: str) -> tuple[dict | None, dict | None]:
    request = Request(
        GOOGLE_USERINFO_ENDPOINT,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(64 * 1024 + 1)
            if len(body) > 64 * 1024:
                return None, {"code": "google_identity_invalid"}
            payload = json.loads(body.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        return None, {"code": "google_identity_unavailable"}

    email = payload.get("email") if isinstance(payload, dict) else None
    email_verified = payload.get("email_verified") if isinstance(payload, dict) else None
    if (
        not isinstance(email, str)
        or not EMAIL_PATTERN.match(email.strip().lower())
        or email_verified is not True
    ):
        return None, {"code": "google_identity_invalid"}
    name = payload.get("name") if isinstance(payload.get("name"), str) else None
    return {
        "email": email.strip().lower(),
        "display_name": name.strip() if isinstance(name, str) and name.strip() else None,
    }, None


def _exchange_microsoft_code(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    tenant: str,
) -> tuple[dict | None, dict | None]:
    request_payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = Request(
        MICROSOFT_TOKEN_ENDPOINT_TEMPLATE.format(tenant=tenant),
        data=request_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
            if len(body) > MAX_OAUTH_RESPONSE_BYTES:
                return None, {"code": "token_exchange_failed", "message": "Microsoft returned an invalid token response."}
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                return None, {"code": "token_exchange_failed", "message": "Microsoft returned an invalid token response."}
            return payload, None
    except HTTPError as error:
        error_body = error.read(MAX_OAUTH_RESPONSE_BYTES + 1).decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}
        return None, {
            "code": "token_exchange_failed",
            "message": (
                parsed_error.get("error_description")
                or parsed_error.get("error")
                or "Microsoft token exchange failed."
            ),
        }
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        return None, {
            "code": "token_exchange_unavailable",
            "message": "Microsoft token exchange was unavailable.",
        }


def _verify_signed_state_with_secrets(
    state: str,
) -> tuple[dict | None, str | None, str | None]:
    shared_secret = os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip()
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    microsoft_client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    candidate_secrets: list[str] = []

    for secret in (shared_secret, google_client_secret, microsoft_client_secret):
        if secret and secret not in candidate_secrets:
            candidate_secrets.append(secret)

    saw_expired_state = False
    for secret in candidate_secrets:
        payload, error = verify_signed_state(
            state,
            secret,
            expected_provider=None,
        )
        if payload is not None:
            return payload, None, secret
        if error == "expired_state":
            saw_expired_state = True

    return None, "expired_state" if saw_expired_state else "invalid_state", None


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            self._send_method_not_allowed(write_body=getattr(self, "command", "") != "HEAD")
            return
        super().send_error(code, message, explain)

    def _send_method_not_allowed(self, *, write_body: bool = True):
        response_body = json.dumps({"ok": False, "error": {"code": "method_not_allowed", "message": "Use GET for OAuth callbacks"}}).encode("utf-8")
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if write_body:
            self.wfile.write(response_body)

    def _send_callback_page(
        self,
        payload: dict,
        *,
        set_cookies: tuple[str, ...] = (),
    ):
        app_redirect_url = getattr(self, "_callback_app_redirect_url", None)
        if not isinstance(app_redirect_url, str) or not app_redirect_url:
            app_redirect_url = _build_app_redirect_url(self.headers)

        if app_redirect_url:
            page = _render_callback_bridge_page(app_redirect_url, payload)
            status_code = 200
        else:
            page = _render_callback_configuration_error_page()
            status_code = 503

        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for cookie in set_cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def _release_callback_mailbox_lease(self):
        lease_token = getattr(self, "_oauth_callback_lease_token", None)
        lease_owner = getattr(self, "_oauth_callback_lease_owner", None)
        lease_mailbox_id = getattr(
            self,
            "_oauth_callback_lease_mailbox_id",
            None,
        )
        self._oauth_callback_lease_token = None
        if not (
            isinstance(lease_token, str)
            and isinstance(lease_owner, str)
            and isinstance(lease_mailbox_id, str)
        ):
            return
        try:
            user_config_store.release_mailbox_mutation_lease(
                lease_owner,
                lease_mailbox_id,
                lease_token,
            )
        except Exception:
            return

    def do_GET(self):
        self._gmail_callback_failure_logged = False
        self._gmail_callback_provider = "google"
        self._gmail_callback_inbox_position = None
        self._oauth_callback_mode = None
        self._oauth_callback_mailbox_id = None
        self._oauth_callback_expected_email = None
        self._oauth_callback_credential_generation = None
        self._oauth_callback_lease_token = None
        self._oauth_callback_lease_owner = None
        self._oauth_callback_lease_mailbox_id = None
        try:
            handler._handle_get(self)
        except Exception:
            if (
                self._gmail_callback_provider == "google"
                and not self._gmail_callback_failure_logged
            ):
                self._gmail_callback_failure_logged = True
                _log_gmail_callback_failure(
                    "unexpected_callback_failure",
                    self._gmail_callback_inbox_position,
                )
            raise
        finally:
            self._release_callback_mailbox_lease()

    def _handle_get(self):
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        oauth_error = params.get("error", [None])[0]
        state = params.get("state", [None])[0]
        member, auth_set_cookies = _resolve_authenticated_member_request(self)
        if member is None:
            member_failure_code = (
                "member_authority_unavailable"
                if auth_set_cookies is _MEMBER_AUTHORITY_UNAVAILABLE
                else "member_unauthenticated"
            )
            if auth_set_cookies is _MEMBER_AUTHORITY_UNAVAILABLE:
                auth_set_cookies = ()
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider="google",
                    email="",
                    connection_status="connection_failed",
                    message="Mailbox authentication session could not be verified. Please try again.",
                    connected=False,
                ),
                failure_code=member_failure_code,
                set_cookies=auth_set_cookies,
            )
            return

        state_payload, state_error, state_signing_secret = _verify_signed_state_with_secrets(
            state or ""
        )

        provider = (
            state_payload.get("provider")
            if isinstance(state_payload, dict)
            and state_payload.get("provider") in {"google", "microsoft"}
            else "google"
        )
        provider_name = "Microsoft" if provider == "microsoft" else "Google"
        self._gmail_callback_provider = provider
        email_hint = (
            state_payload.get("email_hint", state_payload.get("email", ""))
            if state_payload is not None
            else ""
        )
        email = email_hint if provider == "microsoft" else ""

        if state_error:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication could not be verified. Please try again.",
                    connected=False,
                ),
                failure_code=(
                    "state_expired"
                    if state_error == "expired_state"
                    else "state_invalid"
                ),
            )
            return

        inbox_position = state_payload.get("inboxPosition")
        mode = state_payload["mode"]
        credential_generation = state_payload["credential_generation"]
        mailbox_id = state_payload.get("mailboxId")
        expected_email = state_payload.get("expected_email")
        self._oauth_callback_mode = mode
        self._oauth_callback_mailbox_id = mailbox_id
        self._oauth_callback_expected_email = expected_email
        self._oauth_callback_credential_generation = credential_generation
        self._gmail_callback_inbox_position = inbox_position
        if (
            not state_signing_secret
            or not verify_owner_binding(
                state_payload,
                member.email,
                state_signing_secret,
                member_user_id=member.user_id,
                member_workspace_id=member.workspace_id,
            )
        ):
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email="",
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication session could not be verified. Please try again.",
                    connected=False,
                ),
                failure_code="owner_binding_invalid",
                inbox_position=inbox_position,
            )
            return
        state_owner_email = normalize_auth_email(member.email)

        public_app_origin = resolve_public_app_origin(self.headers)
        if not public_app_origin:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email="",
                    connection_status="connection_failed",
                    message="Mailbox authentication could not be completed because the application is not configured safely.",
                    connected=False,
                ),
                failure_code="canonical_origin_invalid",
                inbox_position=inbox_position,
            )
            return
        self._callback_app_redirect_url = f"{public_app_origin}/"

        if oauth_error:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication was cancelled or denied.",
                    connected=False,
                ),
                failure_code="provider_denied",
                inbox_position=inbox_position,
            )
            return

        authorization_code = params.get("code", [None])[0]
        if not authorization_code:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} did not return an authorization code.",
                    connected=False,
                ),
                failure_code="authorization_code_missing",
                inbox_position=inbox_position,
            )
            return

        if mode == "reconnect":
            lease_result = user_config_store.acquire_mailbox_mutation_lease(
                state_owner_email,
                mailbox_id,
            )
            lease_token = lease_result.get("token")
            if lease_result.get("status") != "acquired" or not isinstance(
                lease_token,
                str,
            ):
                lease_held = lease_result.get("status") == "held"
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider="google",
                        email=expected_email,
                        connection_status="connection_failed",
                        message=(
                            "Another reconnect is already in progress for this mailbox."
                            if lease_held
                            else "Mailbox configuration is temporarily unavailable."
                        ),
                        connected=False,
                        mailbox_id=mailbox_id,
                        mode=mode,
                    ),
                    failure_code=(
                        "oauth_reconnect_in_progress"
                        if lease_held
                        else "user_config_store_unavailable"
                    ),
                )
                return
            self._oauth_callback_lease_token = lease_token
            self._oauth_callback_lease_owner = state_owner_email
            self._oauth_callback_lease_mailbox_id = mailbox_id

            reconnect_preflight, reconnect_preflight_error = (
                _prepare_gmail_reconnect_target(
                    member,
                    mailbox_id=mailbox_id,
                    expected_email=expected_email,
                    credential_generation=credential_generation,
                )
            )
            if reconnect_preflight_error or not reconnect_preflight:
                reconnect_failure_code = _resolve_gmail_callback_failure_code(
                    reconnect_preflight_error,
                    default="oauth_reconnect_target_invalid",
                )
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider="google",
                        email=expected_email,
                        connection_status="connection_failed",
                        message=(
                            reconnect_preflight_error.get("message")
                            if isinstance(reconnect_preflight_error, dict)
                            and isinstance(
                                reconnect_preflight_error.get("message"),
                                str,
                            )
                            else "The selected Google mailbox cannot be reconnected safely."
                        ),
                        connected=False,
                        mailbox_id=mailbox_id,
                        mode=mode,
                    ),
                    failure_code=reconnect_failure_code,
                )
                return

        if provider == "google":
            google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
            google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
            google_redirect_uri = f"{public_app_origin}{OAUTH_CALLBACK_PATH}"

            if not google_client_id or not google_client_secret:
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=email,
                        connection_status="connection_failed",
                        message="Google OAuth callback is not configured safely.",
                        connected=False,
                    ),
                    failure_code="token_exchange_unavailable",
                    inbox_position=inbox_position,
                )
                return

            token_payload, token_error = _exchange_google_code(
                code=authorization_code,
                code_verifier=state_payload["code_verifier"],
                client_id=google_client_id,
                client_secret=google_client_secret,
                redirect_uri=google_redirect_uri,
            )
        else:
            microsoft_client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
            microsoft_client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
            microsoft_redirect_uri = os.getenv("MICROSOFT_OAUTH_REDIRECT_URI", "").strip()
            microsoft_tenant = os.getenv("MICROSOFT_OAUTH_TENANT", "").strip() or "common"

            if (
                not microsoft_client_id
                or not microsoft_client_secret
                or not microsoft_redirect_uri
            ):
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=email,
                        connection_status="connection_failed",
                        message="Microsoft OAuth callback is not fully configured.",
                        connected=False,
                    ),
                    failure_code="token_exchange_unavailable",
                    inbox_position=inbox_position,
                )
                return

            token_payload, token_error = _exchange_microsoft_code(
                code=authorization_code,
                code_verifier=state_payload["code_verifier"],
                client_id=microsoft_client_id,
                client_secret=microsoft_client_secret,
                redirect_uri=microsoft_redirect_uri,
                tenant=microsoft_tenant,
            )

        if token_error:
            token_failure_code = _resolve_gmail_callback_failure_code(
                token_error,
                default="token_exchange_failed",
                code_mapping={
                    "token_exchange_failed": "token_exchange_failed",
                    "token_exchange_unavailable": "token_exchange_unavailable",
                },
            )
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication could not be completed. Please try again.",
                    connected=False,
                ),
                failure_code=token_failure_code,
                inbox_position=inbox_position,
            )
            return

        if not token_payload or not token_payload.get("access_token"):
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} returned an incomplete token response.",
                    connected=False,
                ),
                failure_code="token_payload_invalid",
                inbox_position=inbox_position,
            )
            return

        if provider == "google":
            oauth_identity, identity_error = _fetch_verified_google_identity(
                str(token_payload["access_token"]),
            )
            if identity_error or not oauth_identity:
                identity_failure_code = _resolve_gmail_callback_failure_code(
                    identity_error,
                    default="google_identity_invalid",
                    code_mapping={
                        "google_identity_invalid": "google_identity_invalid",
                        "google_identity_unavailable": "google_identity_unavailable",
                    },
                )
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email="",
                        connection_status="connection_failed",
                        message="Google account identity could not be verified. Please try again.",
                        connected=False,
                    ),
                    failure_code=identity_failure_code,
                    inbox_position=inbox_position,
                )
                return
        else:
            oauth_identity = {"email": email.strip().lower(), "display_name": None}
        mailbox_email = oauth_identity["email"]
        display_name = oauth_identity.get("display_name")

        if (
            provider == "google"
            and mode == "reconnect"
            and mailbox_email != expected_email
        ):
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider="google",
                    email=expected_email,
                    connection_status="connection_failed",
                    message=(
                        "Please reconnect using the Google account for "
                        f"{expected_email}."
                    ),
                    connected=False,
                    mailbox_id=mailbox_id,
                    mode=mode,
                ),
                failure_code="oauth_reconnect_email_mismatch",
            )
            return

        if provider == "google":
            current_member, current_member_error = (
                _resolve_current_gmail_callback_member(
                    self,
                    state_payload=state_payload,
                    state_signing_secret=state_signing_secret,
                )
            )
            if current_member_error or current_member is None:
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="connection_failed",
                        message=(
                            "This Gmail inbox could not be linked because the "
                            "member or onboarding session changed."
                        ),
                        connected=False,
                        inbox_position=inbox_position,
                    ),
                    failure_code=(
                        current_member_error or "owner_binding_invalid"
                    ),
                    inbox_position=inbox_position,
                )
                return
            member = current_member
            if mode == "reconnect":
                _, registration_preflight_error = _prepare_gmail_reconnect_target(
                    member,
                    mailbox_id=mailbox_id,
                    expected_email=expected_email,
                    credential_generation=credential_generation,
                )
            else:
                _, registration_preflight_error = (
                    _prepare_gmail_managed_inbox_registration(
                        member,
                        email=mailbox_email,
                        owner_email=state_owner_email,
                        inbox_position=inbox_position,
                    )
                )
            if registration_preflight_error:
                preflight_failure_code = _resolve_gmail_callback_failure_code(
                    registration_preflight_error,
                    default="user_config_preflight_failed",
                    code_mapping={
                        "gmail_link_conflict": "gmail_link_conflict",
                        "unauthorized": "owner_binding_invalid",
                        "user_config_store_unavailable": (
                            "user_config_store_unavailable"
                        ),
                    },
                )
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="connection_failed",
                        message=(
                            registration_preflight_error.get("message")
                            if mode == "reconnect"
                            and isinstance(registration_preflight_error, dict)
                            and isinstance(
                                registration_preflight_error.get("message"),
                                str,
                            )
                            else "This Gmail inbox could not be linked to the selected onboarding inbox."
                        ),
                        connected=False,
                        inbox_position=inbox_position,
                        mailbox_id=mailbox_id,
                        mode=mode,
                    ),
                    failure_code=preflight_failure_code,
                    inbox_position=inbox_position,
                )
                return

            persisted_record, persistence_error = persist_google_token_record(
                email=mailbox_email,
                owner_email=state_owner_email,
                token_payload=token_payload,
                mode=mode,
                credential_generation=credential_generation,
            )
        else:
            persisted_record, persistence_error = persist_microsoft_token_record(
                email=mailbox_email,
                token_payload=token_payload,
            )

        if persistence_error:
            persistence_failure_code = _resolve_gmail_callback_failure_code(
                persistence_error,
                default="token_persistence_failed",
                code_mapping={
                    "invalid_token_owner": "token_payload_invalid",
                    "invalid_token_payload": "token_payload_invalid",
                    "token_owner_conflict": "token_owner_conflict",
                    "token_persistence_failed": "token_persistence_failed",
                },
            )
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=mailbox_email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        persistence_error.get("message")
                        if persistence_failure_code == "refresh_token_missing"
                        and isinstance(persistence_error, dict)
                        and isinstance(persistence_error.get("message"), str)
                        else f"{provider_name} authentication completed, but secure authorization storage is unavailable."
                    ),
                    connected=False,
                    display_name=display_name,
                    mailbox_id=mailbox_id,
                    mode=mode if provider == "google" else None,
                ),
                failure_code=persistence_failure_code,
                inbox_position=inbox_position,
            )
            return

        if not persisted_record:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=mailbox_email,
                    connection_status="authenticated_pending_activation",
                    message=f"{provider_name} authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.",
                    connected=False,
                    display_name=display_name,
                ),
                failure_code="mailbox_readback_verification_failed",
                inbox_position=inbox_position,
            )
            return

        if persisted_record.get("_storage_durable") is not True:
            _send_gmail_callback_failure(
                self,
                _build_callback_payload(
                    provider=provider,
                    email=mailbox_email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        f"{provider_name} authentication completed. Tokens are stored only in the current server runtime bridge. "
                        "Final mailbox activation requires durable secure mailbox token storage."
                    ),
                    connected=False,
                    display_name=display_name,
                ),
                failure_code="token_store_unavailable",
                inbox_position=inbox_position,
            )
            return

        connected_message = (
            f"{provider_name} account connected. Durable mailbox token storage is active."
        )
        saved_mailbox = None
        if provider == "google":
            current_member, current_member_error = (
                _resolve_current_gmail_callback_member(
                    self,
                    state_payload=state_payload,
                    state_signing_secret=state_signing_secret,
                )
            )
            if current_member_error or current_member is None:
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="authenticated_pending_activation",
                        message=(
                            "Google authentication completed, but the current "
                            "member or onboarding session could not be verified."
                        ),
                        connected=False,
                        inbox_position=inbox_position,
                    ),
                    failure_code=(
                        current_member_error or "owner_binding_invalid"
                    ),
                    inbox_position=inbox_position,
                )
                return
            member = current_member
            if mode == "reconnect":
                saved_mailbox, user_config_error = (
                    _register_gmail_reconnect_in_user_config(
                        member,
                        mailbox_id=mailbox_id,
                        expected_email=expected_email,
                        verified_email=mailbox_email,
                        owner_email=state_owner_email,
                        message=connected_message,
                        credential_generation=credential_generation,
                    )
                )
            else:
                saved_mailbox, user_config_error = (
                    _register_gmail_managed_inbox_in_user_config(
                        member,
                        email=mailbox_email,
                        display_name=display_name,
                        owner_email=state_owner_email,
                        message=connected_message,
                        inbox_position=inbox_position,
                    )
                )
            if user_config_error:
                user_config_failure_code = _resolve_gmail_callback_failure_code(
                    user_config_error,
                    default="user_config_write_failed",
                    code_mapping={
                        "gmail_link_conflict": "gmail_link_conflict",
                        "unauthorized": "owner_binding_invalid",
                        "user_config_store_unavailable": (
                            "user_config_store_unavailable"
                        ),
                    },
                )
                _send_gmail_callback_failure(
                    self,
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="authenticated_pending_activation",
                        message=(
                            user_config_error.get("message")
                            if mode == "reconnect"
                            and isinstance(user_config_error, dict)
                            and isinstance(user_config_error.get("message"), str)
                            else "Google authentication completed, but the Gmail inbox could not be saved securely."
                        ),
                        connected=False,
                        inbox_position=inbox_position,
                        mailbox_id=mailbox_id,
                        mode=mode,
                    ),
                    failure_code=user_config_failure_code,
                    inbox_position=inbox_position,
                )
                return

        callback_payload = _build_callback_payload(
            provider=provider,
            email=mailbox_email,
            connection_status="connected",
            message=connected_message,
            connected=True,
            display_name=display_name,
            inbox_position=inbox_position,
            mailbox_id=(
                saved_mailbox.get("id")
                if isinstance(saved_mailbox, dict)
                else None
            ),
            mode=mode if provider == "google" else None,
        )
        if provider == "google" and callback_payload.get("status") != "success":
            _send_gmail_callback_failure(
                self,
                callback_payload,
                failure_code="mailbox_readback_verification_failed",
                inbox_position=inbox_position,
            )
            return
        self._send_callback_page(callback_payload)

    def do_POST(self):
        self._send_method_not_allowed()

    def do_PUT(self):
        self._send_method_not_allowed()

    def do_PATCH(self):
        self._send_method_not_allowed()

    def do_DELETE(self):
        self._send_method_not_allowed()

    def do_HEAD(self):
        self._send_method_not_allowed(write_body=False)

    def do_OPTIONS(self):
        response_body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        return
