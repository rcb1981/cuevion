import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
STATE_MAX_AGE_SECONDS = 15 * 60


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def verify_signed_state(
    state: str,
    signing_secret: str,
    expected_provider: str = "google",
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

    if payload.get("provider") != expected_provider:
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
    rest_url = (
        os.getenv("KV_REST_API_URL", "").strip()
        or os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
    )
    rest_token = (
        os.getenv("KV_REST_API_TOKEN", "").strip()
        or os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
    )

    if not rest_url or not rest_token:
        return None

    return {
        "backend": "vercel_kv_rest"
        if os.getenv("KV_REST_API_URL", "").strip()
        else "upstash_redis_rest",
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


def _build_store_key(provider: str, email: str) -> str:
    return f"cuevion:oauth_tokens:{provider}:{email.strip().lower()}"


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
        f"/set/{quote(store_key, safe='')}",
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
    store_key = _build_store_key("google", normalized_email)
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
) -> dict:
    return {
        "provider": provider,
        "email": email,
        "connectionMethod": "oauth",
        "connectionStatus": connection_status,
        "connected": connected,
        "message": message,
    }


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
        google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        oauth_state_secret = (
            os.getenv("CUEVION_OAUTH_STATE_SECRET", "").strip() or google_client_secret
        )

        state_payload, state_error = verify_signed_state(
            state or "",
            oauth_state_secret,
        )

        provider = "google"
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
                    message="Google authentication could not be verified. Please try again.",
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
                    message="Google authentication was cancelled or denied.",
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
                    message="Google did not return an authorization code.",
                    connected=False,
                )
            )
            return

        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
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
                    message="Google returned an incomplete token response.",
                    connected=False,
                )
            )
            return

        persisted_record, persistence_error = persist_google_token_record(
            email=email,
            token_payload=token_payload,
        )

        if persistence_error:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        persistence_error["message"]
                        or "Google authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage."
                    ),
                    connected=False,
                )
            )
            return

        if not persisted_record:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message="Google authentication completed. Tokens are stored only in the current server runtime. Final mailbox activation requires durable secure mailbox token storage.",
                    connected=False,
                )
            )
            return

        if persisted_record.get("_storage_durable") is not True:
            self._send_callback_page(
                _build_callback_payload(
                    provider=provider,
                    email=email,
                    connection_status="authenticated_pending_activation",
                    message=(
                        "Google authentication completed. Tokens are stored only in the current server runtime bridge. "
                        "Final mailbox activation requires durable secure mailbox token storage."
                    ),
                    connected=False,
                )
            )
            return

        self._send_callback_page(
            _build_callback_payload(
                provider=provider,
                email=email,
                connection_status="connected",
                message="Google account connected. Durable mailbox token storage is active.",
                connected=True,
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
                        "message": "Use GET for Google OAuth callbacks",
                    },
                }
            ).encode("utf-8")
        )

    def log_message(self, format, *args):
        return
