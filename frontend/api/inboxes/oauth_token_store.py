import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

GMAIL_OAUTH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60


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


def is_google_token_store_durable() -> bool:
    return _resolve_durable_store_config() is not None


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


def get_google_token_record(email: str) -> dict | None:
    normalized_email = email.strip().lower()
    store_key = _build_store_key(normalized_email)
    durable_config = _resolve_durable_store_config()

    if durable_config:
        record, _ = _read_durable_record(durable_config, store_key)
        if isinstance(record, dict):
            return record

    runtime_store = _read_runtime_store(_resolve_runtime_store_path())
    record = runtime_store.get(store_key)
    return record if isinstance(record, dict) else None


def get_google_token_record_with_metadata(email: str) -> dict | None:
    normalized_email = email.strip().lower()
    store_key = _build_store_key(normalized_email)
    durable_config = _resolve_durable_store_config()

    if durable_config:
        record, _ = _read_durable_record(durable_config, store_key)
        if isinstance(record, dict):
            return {
                **record,
                "_storage_backend": durable_config["backend"],
                "_storage_durable": True,
            }

    runtime_store = _read_runtime_store(_resolve_runtime_store_path())
    record = runtime_store.get(store_key)
    if not isinstance(record, dict):
        return None

    return {
        **record,
        "_storage_backend": "runtime_tmp_file",
        "_storage_durable": False,
    }
