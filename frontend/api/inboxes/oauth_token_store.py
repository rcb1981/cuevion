import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _resolve_store_path() -> Path:
    configured_path = os.getenv("CUEVION_GMAIL_TOKEN_STORE_PATH", "").strip()
    if configured_path:
        return Path(configured_path)

    return Path(tempfile.gettempdir()) / "cuevion-gmail-oauth-token-store.json"


def is_google_token_store_durable() -> bool:
    configured_value = os.getenv("CUEVION_GMAIL_TOKEN_STORE_DURABLE", "").strip().lower()
    return configured_value in {"1", "true", "yes", "on"}


def _read_store(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _write_store(path: Path, payload: dict) -> None:
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
    store_path = _resolve_store_path()
    store = _read_store(store_path)
    existing_record = store.get(f"google:{normalized_email}")
    next_record = build_google_token_record(
        email=normalized_email,
        token_payload=token_payload,
        existing_record=existing_record if isinstance(existing_record, dict) else None,
    )
    store[f"google:{normalized_email}"] = next_record

    try:
        _write_store(store_path, store)
    except OSError as error:
        return None, {
            "code": "token_persistence_failed",
            "message": f"Google authentication succeeded, but mailbox token storage failed: {error}",
        }

    persisted_store = _read_store(store_path)
    persisted_record = persisted_store.get(f"google:{normalized_email}")
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

    return persisted_record, None


def get_google_token_record(email: str) -> dict | None:
    normalized_email = email.strip().lower()
    store = _read_store(_resolve_store_path())
    record = store.get(f"google:{normalized_email}")
    return record if isinstance(record, dict) else None
