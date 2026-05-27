import base64
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
MICROSOFT_TOKEN_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
)
STATE_MAX_AGE_SECONDS = 15 * 60
GMAIL_OAUTH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
USER_CONFIG_SCHEMA_VERSION = 1
USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from oauth_token_store import persist_microsoft_token_record
from beta_auth import (
    normalize_auth_email,
    parse_beta_session_token,
    read_beta_session_cookie,
    resolve_beta_session_secret,
)


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


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
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest(),
    )

    if not hmac.compare_digest(signature, expected_signature):
        return None, "invalid_state"

    try:
        payload = json.loads(base64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "invalid_state"

    if expected_provider is not None and payload.get("provider") != expected_provider:
        return None, "invalid_state"

    issued_at = payload.get("issued_at")
    if not isinstance(issued_at, int):
        return None, "invalid_state"

    if int(time.time()) - issued_at > STATE_MAX_AGE_SECONDS:
        return None, "expired_state"

    if not isinstance(payload.get("code_verifier"), str) or not payload.get("code_verifier"):
        return None, "invalid_state"

    if not isinstance(payload.get("email"), str):
        return None, "invalid_state"

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
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}, None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}

        return None, {
            "code": "token_persistence_failed",
            "message": (
                parsed_error.get("error")
                or parsed_error.get("message")
                or f"Durable mailbox token storage failed with HTTP {error.code}."
            ),
        }
    except URLError as error:
        return None, {
            "code": "token_persistence_failed",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the durable mailbox token store."
            ),
        }


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, error

    result = payload.get("result") if isinstance(payload, dict) else None
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None, {
                "code": "token_persistence_failed",
                "message": "Durable mailbox token storage returned an unreadable token record.",
            }
        return parsed if isinstance(parsed, dict) else None, None

    return result if isinstance(result, dict) else None, None


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
    except OSError as error:
        return None, {
            "code": "token_persistence_failed",
            "message": f"Google authentication succeeded, but mailbox token storage failed: {error}",
        }

    persisted_store = _read_runtime_store(store_path)
    persisted_record = persisted_store.get(store_key)
    return persisted_record if isinstance(persisted_record, dict) else None, None


def persist_google_token_record(
    *,
    email: str,
    token_payload: dict,
) -> tuple[dict | None, dict | None]:
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None, {
            "code": "invalid_token_payload",
            "message": "Google returned an incomplete token response.",
        }

    normalized_email = email.strip().lower()
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

    next_record = build_google_token_record(
        email=normalized_email,
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


def _get_authenticated_user(headers) -> dict | None:
    if not resolve_beta_session_secret():
        return None

    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _build_user_config_key(email: str) -> str:
    return f"{USER_CONFIG_KEY_PREFIX}:{normalize_auth_email(email)}"


def _extract_oauth_identity(token_payload: dict, fallback_email: str) -> dict:
    identity = {
        "email": fallback_email.strip().lower(),
        "display_name": None,
    }
    id_token = token_payload.get("id_token")
    if not isinstance(id_token, str) or id_token.count(".") < 2:
        return identity

    try:
        payload_segment = id_token.split(".")[1]
        claims = json.loads(base64url_decode(payload_segment).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return identity

    email = claims.get("email") if isinstance(claims, dict) else None
    if isinstance(email, str) and EMAIL_PATTERN.match(email.strip().lower()):
        identity["email"] = email.strip().lower()

    name = claims.get("name") if isinstance(claims, dict) else None
    if isinstance(name, str) and name.strip():
        identity["display_name"] = name.strip()

    return identity


def _format_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    if not local_part:
        return email

    return " ".join(part.capitalize() for part in local_part.split())


def _build_gmail_managed_inbox_id(email: str, existing_ids: set[str]) -> str:
    local_part = email.split("@", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local_part).strip("-") or "gmail"
    candidate = f"gmail-{slug}"
    if candidate not in existing_ids:
        return candidate

    domain_slug = re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-") or "gmail"
    candidate = f"gmail-{domain_slug}"
    suffix = 2
    while candidate in existing_ids:
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


def _upsert_gmail_managed_inbox_record(
    managed_inboxes: list,
    *,
    email: str,
    display_name: str | None,
    message: str,
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
            and mailbox_provider in {"google", "gmail", None}
        ):
            matched_index = index
            break

    title = (display_name or "").strip() or _format_name_from_email(normalized_email)
    safe_defaults = {
        "title": title,
        "email": normalized_email,
        "provider": "google",
        "connected": True,
        "connectionMethod": "oauth",
        "connectionStatus": "connected",
        "connectionMessage": message,
        "oauthAuthorizationUrl": None,
        "customImap": _create_empty_managed_imap_settings(),
        "customSmtp": _create_empty_managed_smtp_settings(),
    }

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
    return _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}",
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8"),
    )


def _upsert_gmail_managed_inbox_in_user_config(
    headers,
    *,
    email: str,
    display_name: str | None,
    message: str,
) -> dict | None:
    session_user = _get_authenticated_user(headers)
    durable_config = _resolve_durable_store_config()
    if not session_user or not durable_config:
        return None

    store_key = _build_user_config_key(session_user["email"])
    existing_record, existing_error = _read_durable_record(durable_config, store_key)
    if existing_error:
        return existing_error

    existing_config = existing_record if isinstance(existing_record, dict) else {}
    existing_managed_inboxes = existing_config.get("managedInboxes")
    if not isinstance(existing_managed_inboxes, list):
        existing_managed_inboxes = []

    next_record = {
        "v": USER_CONFIG_SCHEMA_VERSION,
        "email": normalize_auth_email(session_user["email"]),
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
    next_record["email"] = normalize_auth_email(session_user["email"])
    next_record["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    next_record["managedInboxes"] = _upsert_gmail_managed_inbox_record(
        existing_managed_inboxes,
        email=email,
        display_name=display_name,
        message=message,
    )

    _, write_error = _write_user_config_durable_record(
        durable_config,
        store_key,
        next_record,
    )
    return write_error

OAUTH_CALLBACK_RESULT_STORAGE_KEY = "cuevion-oauth-callback-result"


def _build_app_redirect_url(headers) -> str:
    configured_app_url = os.getenv("CUEVION_APP_URL", "").strip()
    if configured_app_url:
        return configured_app_url

    host = (
        headers.get("x-forwarded-host")
        or headers.get("host")
        or "localhost:3000"
    )
    protocol = headers.get("x-forwarded-proto")
    if not protocol:
        protocol = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"

    return f"{protocol}://{host}/"


def _build_callback_payload(
    *,
    provider: str,
    email: str,
    connection_status: str,
    message: str,
    connected: bool,
    display_name: str | None = None,
) -> dict:
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
    redirect_json = json.dumps(app_redirect_url)
    storage_key_json = json.dumps(OAUTH_CALLBACK_RESULT_STORAGE_KEY)
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cuevion Gmail Connection</title>
  </head>
  <body>
    <script>
      const payload = {payload_json};
      const redirectUrl = {redirect_json};
      window.localStorage.setItem({storage_key_json}, JSON.stringify(payload));
      window.location.replace(redirectUrl);
    </script>
    <p>Returning to Cuevion…</p>
  </body>
</html>
"""
    return html.encode("utf-8")


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
            return json.loads(response.read().decode("utf-8")), None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
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
    except URLError as error:
        return None, {
            "code": "token_exchange_unavailable",
            "message": str(error.reason) if getattr(error, "reason", None) else "Could not reach Google.",
        }


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
            return json.loads(response.read().decode("utf-8")), None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
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
    except URLError as error:
        return None, {
            "code": "token_exchange_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach Microsoft."
            ),
        }


def _verify_signed_state_with_secrets(state: str) -> tuple[dict | None, str | None]:
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
            return payload, None
        if error == "expired_state":
            saw_expired_state = True

    return None, "expired_state" if saw_expired_state else "invalid_state"


class handler(BaseHTTPRequestHandler):
    def _send_callback_page(self, payload: dict):
        page = _render_callback_bridge_page(
            _build_app_redirect_url(self.headers),
            payload,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        params = parse_qs(parsed_url.query)
        oauth_error = params.get("error", [None])[0]
        state = params.get("state", [None])[0]
        state_payload, state_error = _verify_signed_state_with_secrets(state or "")

        provider = (
            state_payload.get("provider")
            if isinstance(state_payload, dict)
            and state_payload.get("provider") in {"google", "microsoft"}
            else "google"
        )
        provider_name = "Microsoft" if provider == "microsoft" else "Google"
        email = (
            state_payload.get("email", "")
            if state_payload is not None
            else ""
        )

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
            google_redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()

            if not google_client_id or not google_client_secret or not google_redirect_uri:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=email,
                        connection_status="connection_failed",
                        message="Google OAuth callback is not fully configured.",
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
                    message=token_error["message"],
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

        oauth_identity = (
            _extract_oauth_identity(token_payload, email)
            if provider == "google"
            else {"email": email.strip().lower(), "display_name": None}
        )
        mailbox_email = oauth_identity["email"] or email.strip().lower()
        display_name = oauth_identity.get("display_name")

        if provider == "google":
            persisted_record, persistence_error = persist_google_token_record(
                email=mailbox_email,
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
                    message=(
                        persistence_error["message"]
                        or f"{provider_name} authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage."
                    ),
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
        if provider == "google":
            user_config_error = _upsert_gmail_managed_inbox_in_user_config(
                self.headers,
                email=mailbox_email,
                display_name=display_name,
                message=connected_message,
            )
            if user_config_error:
                self._send_callback_page(
                    _build_callback_payload(
                        provider=provider,
                        email=mailbox_email,
                        connection_status="authenticated_pending_activation",
                        message=(
                            user_config_error.get("message")
                            or "Google authentication completed, but the Gmail inbox could not be saved to user config."
                        ),
                        connected=False,
                        display_name=display_name,
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
            )
        )

    def do_POST(self):
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "method_not_allowed",
                        "message": "Use GET for OAuth callbacks",
                    },
                }
            ).encode("utf-8")
        )

    def log_message(self, format, *args):
        return
