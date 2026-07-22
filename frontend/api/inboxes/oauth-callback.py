import base64
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import time
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
GMAIL_OAUTH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
USER_CONFIG_SCHEMA_VERSION = 1
USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
OAUTH_STATE_VERSION = 1
MAX_STATE_CLOCK_SKEW_SECONDS = 60
STATE_SIGNATURE_DOMAIN = "cuevion-oauth-state-signature:v1"
OWNER_BINDING_DOMAIN = "cuevion-oauth-owner-binding:v1"
PKCE_DERIVATION_DOMAIN = "cuevion-oauth-pkce:v1"
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
    provider: str,
    email_hint: str,
    nonce: str,
    issued_at: int,
    expires_at: int,
    signing_secret: str,
    inbox_position: str | None = None,
) -> str:
    binding_fields = [
        OWNER_BINDING_DOMAIN,
        str(OAUTH_STATE_VERSION),
        normalize_auth_email(owner_email),
        provider,
        email_hint,
    ]
    if inbox_position is not None:
        binding_fields.append(inbox_position)
    binding_fields.extend((nonce, str(issued_at), str(expires_at)))
    binding_message = "\n".join(binding_fields)
    return base64url_encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            binding_message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )


def verify_owner_binding(payload: dict, owner_email: str, signing_secret: str) -> bool:
    expected_binding = build_owner_binding(
        owner_email=owner_email,
        provider=payload["provider"],
        email_hint=payload["email_hint"],
        nonce=payload["nonce"],
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
        signing_secret=signing_secret,
        inbox_position=payload.get("inboxPosition"),
    )
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
        "issued_at",
        "expires_at",
        "nonce",
        "owner_binding",
        "inboxPosition",
    }
    required_state_fields = allowed_state_fields - {"inboxPosition"}
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
    nonce = payload.get("nonce")
    owner_binding = payload.get("owner_binding")
    if (
        not isinstance(email_hint, str)
        or email_hint != email_hint.strip().lower()
        or (email_hint and not EMAIL_PATTERN.match(email_hint))
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


def build_google_token_record(
    *,
    email: str,
    owner_email: str,
    token_payload: dict,
    existing_record: dict | None = None,
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
    token_type = token_payload.get("token_type")
    now = datetime.now(timezone.utc).isoformat()

    return {
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


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, error

    if not isinstance(payload, dict) or "result" not in payload:
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox token storage returned an unreadable token record.",
        }
    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None, {
                "code": "token_persistence_failed",
                "message": "Durable mailbox token storage returned an unreadable token record.",
            }
        if not isinstance(parsed, dict):
            return None, {
                "code": "token_persistence_failed",
                "message": "Durable mailbox token storage returned an unreadable token record.",
            }
        return parsed, None

    if not isinstance(result, dict):
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox token storage returned an unreadable token record.",
        }
    return result, None


def _write_durable_record(
    config: dict,
    store_key: str,
    record: dict,
) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}?EX={GMAIL_OAUTH_TOKEN_TTL_SECONDS}",
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )
    if error:
        return None, error

    if not isinstance(payload, dict) or payload.get("result") != "OK":
        return None, {
            "code": "token_persistence_failed",
            "message": "Durable mailbox token storage did not confirm the write.",
        }

    verified_record, verify_error = _read_durable_record(config, store_key)
    if verify_error:
        return None, verify_error

    return verified_record, None


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
) -> tuple[dict | None, dict | None]:
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None, {
            "code": "invalid_token_payload",
            "message": "Google returned an incomplete token response.",
        }

    normalized_email = email.strip().lower()
    normalized_owner_email = owner_email.strip().lower()
    if not EMAIL_PATTERN.match(normalized_owner_email):
        return None, {
            "code": "invalid_token_owner",
            "message": "Authenticated Gmail token ownership is required.",
        }
    store_key = _build_store_key(normalized_email)
    durable_config = _resolve_durable_store_config()
    existing_record = None

    if durable_config:
        existing_record, existing_error = _read_durable_record(durable_config, store_key)
        if existing_error:
            return None, existing_error
    else:
        existing_store = _read_runtime_store(_resolve_runtime_store_path())
        existing_record = existing_store.get(store_key)

    if isinstance(existing_record, dict):
        existing_owner = existing_record.get("owner_email")
        if (
            existing_record.get("provider") != "google"
            or existing_record.get("email") != normalized_email
            or not isinstance(existing_owner, str)
            or not EMAIL_PATTERN.match(normalize_auth_email(existing_owner))
            or normalize_auth_email(existing_owner) != normalized_owner_email
        ):
            return None, {
                "code": "token_owner_conflict",
                "message": "This Google mailbox is already linked to another account owner.",
            }

    next_record = build_google_token_record(
        email=normalized_email,
        owner_email=normalized_owner_email,
        token_payload=token_payload,
        existing_record=existing_record if isinstance(existing_record, dict) else None,
    )

    if durable_config:
        persisted_record, error = _write_durable_record(
            durable_config,
            store_key,
            next_record,
        )
        storage_backend = durable_config["backend"]
        storage_durable = True
    else:
        persisted_record, error = _persist_runtime_record(store_key, next_record)
        storage_backend = "runtime_tmp_file"
        storage_durable = False

    if error:
        return None, error

    if not isinstance(persisted_record, dict):
        return None, {
            "code": "token_persistence_failed",
            "message": "Google authentication succeeded, but mailbox token storage could not be verified.",
        }

    if (
        persisted_record.get("provider") != "google"
        or persisted_record.get("email") != normalized_email
        or persisted_record.get("owner_email") != normalized_owner_email
        or not isinstance(persisted_record.get("access_token"), str)
        or not persisted_record.get("access_token")
    ):
        return None, {
            "code": "token_persistence_failed",
            "message": "Google authentication succeeded, but the stored mailbox token record is incomplete.",
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
        return None, ()


def _build_user_config_key(email: str) -> str:
    return f"{USER_CONFIG_KEY_PREFIX}:{normalize_auth_email(email)}"


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


def _resolve_gmail_managed_inbox_target(
    managed_inboxes: list,
    *,
    email: str,
    inbox_position: str | None,
) -> tuple[int | None, dict | None]:
    normalized_email = email.strip().lower()
    if not EMAIL_PATTERN.match(normalized_email):
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

    return email_match if email_match is not None else position_match, None


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


def _write_user_config_durable_record(
    config: dict,
    store_key: str,
    record: dict,
) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}",
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )
    if error:
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage is temporarily unavailable.",
        }
    if not isinstance(payload, dict) or payload.get("result") != "OK":
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage did not confirm the write.",
        }
    return payload, None


def _read_user_config_durable_record(
    config: dict,
    store_key: str,
    *,
    allow_missing: bool = False,
) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage is temporarily unavailable.",
        }
    if not isinstance(payload, dict) or "result" not in payload:
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage could not verify the saved mailbox.",
        }
    result = payload.get("result")
    if result is None and allow_missing:
        return None, None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            result = None
    if not isinstance(result, dict):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage could not verify the saved mailbox.",
        }
    return result, None


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
        }
    durable_config = _resolve_durable_store_config()
    if not durable_config:
        return None, {
            "code": "user_config_store_unavailable",
            "message": "User config storage is unavailable.",
        }

    store_key = _build_user_config_key(member.email)
    existing_record, existing_error = _read_user_config_durable_record(
        durable_config,
        store_key,
        allow_missing=True,
    )
    if existing_error:
        return None, existing_error

    existing_config = existing_record if isinstance(existing_record, dict) else {}
    stored_owner = existing_config.get("email")
    if stored_owner is not None and (
        not isinstance(stored_owner, str)
        or normalize_auth_email(stored_owner) != normalize_auth_email(member.email)
    ):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config ownership could not be verified.",
        }

    existing_managed_inboxes = existing_config.get("managedInboxes")
    if "managedInboxes" not in existing_config:
        existing_managed_inboxes = []
    elif not isinstance(existing_managed_inboxes, list):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "Existing managed inbox configuration is malformed.",
        }

    _, conflict_error = _resolve_gmail_managed_inbox_target(
        existing_managed_inboxes,
        email=email,
        inbox_position=inbox_position,
    )
    if conflict_error:
        return None, conflict_error

    return {
        "durable_config": durable_config,
        "store_key": store_key,
        "existing_config": existing_config,
        "existing_managed_inboxes": existing_managed_inboxes,
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
    preparation, preparation_error = _prepare_gmail_managed_inbox_registration(
        member,
        email=email,
        owner_email=owner_email,
        inbox_position=inbox_position,
    )
    if preparation_error or not preparation:
        return None, preparation_error

    durable_config = preparation["durable_config"]
    store_key = preparation["store_key"]
    existing_config = preparation["existing_config"]
    existing_managed_inboxes = preparation["existing_managed_inboxes"]

    next_record = {
        "v": USER_CONFIG_SCHEMA_VERSION,
        "email": normalize_auth_email(member.email),
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
    next_record["email"] = normalize_auth_email(member.email)
    next_record["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
            "message": "User config storage could not prepare the verified Gmail mailbox.",
        }
    intended_mailbox = intended_mailboxes[0]

    _, write_error = _write_user_config_durable_record(
        durable_config,
        store_key,
        next_record,
    )
    if write_error:
        return None, write_error

    verified_record, verify_error = _read_user_config_durable_record(
        durable_config,
        store_key,
    )
    if verify_error:
        return None, verify_error
    if not _verify_saved_gmail_mailbox(
        verified_record,
        intended_mailbox,
        owner_email,
        next_record["updatedAt"],
    ):
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage could not verify the saved Gmail mailbox.",
        }
    verified_inboxes = verified_record.get("managedInboxes")
    saved_mailboxes = [
        mailbox
        for mailbox in verified_inboxes
        if isinstance(mailbox, dict) and mailbox.get("id") == intended_mailbox.get("id")
    ] if isinstance(verified_inboxes, list) else []
    if len(saved_mailboxes) != 1:
        return None, {
            "code": "user_config_persistence_failed",
            "message": "User config storage could not verify the saved Gmail mailbox.",
        }
    return dict(saved_mailboxes[0]), None


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
        normalized_email = email.strip().lower() if isinstance(email, str) else ""
        if EMAIL_PATTERN.match(normalized_email):
            payload["email"] = normalized_email
        if isinstance(inbox_position, str) and (
            inbox_position in ONBOARDING_PRESET_INBOX_IDS
            or ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(inbox_position)
            is not None
        ):
            payload["inboxPosition"] = inbox_position
        if is_success:
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
    <script>
      const payload = {payload_json};
      const redirectUrl = {redirect_json};
      window.history.replaceState(null, "", {callback_path_json});
      window.localStorage.setItem({storage_key_json}, JSON.stringify(payload));
      window.location.replace(redirectUrl);
    </script>
    <p>Returning to Cuevion…</p>
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

    def do_GET(self):
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        oauth_error = params.get("error", [None])[0]
        state = params.get("state", [None])[0]
        member, auth_set_cookies = _resolve_authenticated_member_request(self)
        if member is None:
            self._send_callback_page(
                _build_callback_payload(
                    provider="google",
                    email="",
                    connection_status="connection_failed",
                    message="Mailbox authentication session could not be verified. Please try again.",
                    connected=False,
                ),
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
        email_hint = (
            state_payload.get("email_hint", state_payload.get("email", ""))
            if state_payload is not None
            else ""
        )
        email = email_hint if provider == "microsoft" else ""

        if state_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication could not be verified. Please try again.",
                    connected=False,
                )
            )
            return

        if (
            not state_signing_secret
            or not verify_owner_binding(
                state_payload,
                member.email,
                state_signing_secret,
            )
        ):
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email="",
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication session could not be verified. Please try again.",
                    connected=False,
                )
            )
            return
        state_owner_email = normalize_auth_email(member.email)
        inbox_position = state_payload.get("inboxPosition")

        public_app_origin = resolve_public_app_origin(self.headers)
        if not public_app_origin:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email="",
                    connection_status="connection_failed",
                    message="Mailbox authentication could not be completed because the application is not configured safely.",
                    connected=False,
                )
            )
            return
        self._callback_app_redirect_url = f"{public_app_origin}/"

        if oauth_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication was cancelled or denied.",
                    connected=False,
                )
            )
            return

        authorization_code = params.get("code", [None])[0]
        if not authorization_code:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} did not return an authorization code.",
                    connected=False,
                )
            )
            return

        if provider == "google":
            google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
            google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
            google_redirect_uri = f"{public_app_origin}{OAUTH_CALLBACK_PATH}"

            if not google_client_id or not google_client_secret:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=email,
                        connection_status="connection_failed",
                        message="Google OAuth callback is not configured safely.",
                        connected=False,
                    )
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
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=email,
                        connection_status="connection_failed",
                        message="Microsoft OAuth callback is not fully configured.",
                        connected=False,
                    )
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
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} authentication could not be completed. Please try again.",
                    connected=False,
                )
            )
            return

        if not token_payload or not token_payload.get("access_token"):
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="connection_failed",
                    message=f"{provider_name} returned an incomplete token response.",
                    connected=False,
                )
            )
            return

        if provider == "google":
            oauth_identity, identity_error = _fetch_verified_google_identity(
                str(token_payload["access_token"]),
            )
            if identity_error or not oauth_identity:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email="",
                        connection_status="connection_failed",
                        message="Google account identity could not be verified. Please try again.",
                        connected=False,
                    )
                )
                return
        else:
            oauth_identity = {"email": email.strip().lower(), "display_name": None}
        mailbox_email = oauth_identity["email"]
        display_name = oauth_identity.get("display_name")

        if provider == "google":
            _, registration_preflight_error = _prepare_gmail_managed_inbox_registration(
                member,
                email=mailbox_email,
                owner_email=state_owner_email,
                inbox_position=inbox_position,
            )
            if registration_preflight_error:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="connection_failed",
                        message="This Gmail inbox could not be linked to the selected onboarding inbox.",
                        connected=False,
                        inbox_position=inbox_position,
                    )
                )
                return

            persisted_record, persistence_error = persist_google_token_record(
                email=mailbox_email,
                owner_email=state_owner_email,
                token_payload=token_payload,
            )
        else:
            persisted_record, persistence_error = persist_microsoft_token_record(
                email=mailbox_email,
                token_payload=token_payload,
            )

        if persistence_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=mailbox_email,
                    connection_status="authenticated_pending_activation",
                    message=f"{provider_name} authentication completed, but secure authorization storage is unavailable.",
                    connected=False,
                    display_name=display_name,
                )
            )
            return

        if not persisted_record:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=mailbox_email,
                    connection_status="authenticated_pending_activation",
                    message=f"{provider_name} authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.",
                    connected=False,
                    display_name=display_name,
                )
            )
            return

        if persisted_record.get("_storage_durable") is not True:
            self._send_callback_page(
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
                )
            )
            return

        connected_message = (
            f"{provider_name} account connected. Durable mailbox token storage is active."
        )
        saved_mailbox = None
        if provider == "google":
            saved_mailbox, user_config_error = _register_gmail_managed_inbox_in_user_config(
                member,
                email=mailbox_email,
                display_name=display_name,
                owner_email=state_owner_email,
                message=connected_message,
                inbox_position=inbox_position,
            )
            if user_config_error:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="authenticated_pending_activation",
                        message="Google authentication completed, but the Gmail inbox could not be saved securely.",
                        connected=False,
                        inbox_position=inbox_position,
                    )
                )
                return

        self._send_callback_page(
            _build_callback_payload(
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
            )
        )

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
