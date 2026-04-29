from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import normalize_auth_email  # noqa: E402

MAILBOX_SECRET_SCHEMA_VERSION = 1
MAILBOX_SECRET_KEY_PREFIX = "cuevion:mailbox-secret:v1"


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


def _normalize_mailbox_id(mailbox_id: str) -> str:
    return str(mailbox_id or "").strip()


def _is_storable_mailbox_id(mailbox_id: str) -> bool:
    normalized_mailbox_id = _normalize_mailbox_id(mailbox_id)
    return bool(normalized_mailbox_id) and not normalized_mailbox_id.startswith("draft-")


def build_mailbox_secret_key(owner_email: str, mailbox_id: str) -> str:
    return (
        f"{MAILBOX_SECRET_KEY_PREFIX}:"
        f"{normalize_auth_email(owner_email)}:{_normalize_mailbox_id(mailbox_id)}"
    )


def _build_error(message: str) -> dict:
    return {
        "code": "mailbox_secret_store_unavailable",
        "message": message,
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

        return None, _build_error(
            parsed_error.get("error")
            or parsed_error.get("message")
            or f"Mailbox secret store request failed with HTTP {error.code}.",
        )
    except URLError as error:
        return None, _build_error(
            str(error.reason)
            if getattr(error, "reason", None)
            else "Could not reach the mailbox secret store.",
        )


def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
    payload, error = _perform_rest_request(
        config,
        "GET",
        f"/get/{quote(store_key, safe='')}",
    )
    if error:
        return None, error

    if not isinstance(payload, dict):
        return None, _build_error("Mailbox secret store returned an unreadable response.")

    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None, _build_error("Mailbox secret store returned malformed JSON.")
        return parsed if isinstance(parsed, dict) else None, None

    return result if isinstance(result, dict) else None, None


def _write_durable_record(config: dict, store_key: str, record: dict) -> tuple[dict | None, dict | None]:
    encoded_record = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}",
        body=encoded_record,
    )


def _normalize_secret_record(record: dict | None, mailbox_id: str) -> dict | None:
    if not isinstance(record, dict):
        return None

    normalized_mailbox_id = _normalize_mailbox_id(mailbox_id)
    imap_password = record.get("imapPassword")
    smtp_password = record.get("smtpPassword")

    return {
        "v": MAILBOX_SECRET_SCHEMA_VERSION,
        "mailboxId": normalized_mailbox_id,
        "updatedAt": record.get("updatedAt") if isinstance(record.get("updatedAt"), str) else None,
        "imapPassword": imap_password if isinstance(imap_password, str) else "",
        "smtpPassword": smtp_password if isinstance(smtp_password, str) else "",
    }


def get_mailbox_secret(owner_email: str, mailbox_id: str) -> dict | None:
    if not _is_storable_mailbox_id(mailbox_id):
        return None

    config = _resolve_durable_store_config()
    if not config:
        return None

    record, error = _read_durable_record(
        config,
        build_mailbox_secret_key(owner_email, mailbox_id),
    )
    if error:
        return None

    return _normalize_secret_record(record, mailbox_id)


def get_mailbox_secret_statuses(owner_email: str, mailbox_ids: list[str]) -> dict[str, dict]:
    statuses: dict[str, dict] = {}

    for mailbox_id in mailbox_ids:
        normalized_mailbox_id = _normalize_mailbox_id(mailbox_id)
        if not normalized_mailbox_id:
            continue

        secret_record = get_mailbox_secret(owner_email, normalized_mailbox_id)
        statuses[normalized_mailbox_id] = {
            "imapPasswordSet": bool(secret_record and secret_record.get("imapPassword")),
            "smtpPasswordSet": bool(secret_record and secret_record.get("smtpPassword")),
        }

    return statuses


def save_mailbox_secret(
    owner_email: str,
    mailbox_id: str,
    imap_password: str | None = None,
    smtp_password: str | None = None,
) -> tuple[dict | None, dict | None]:
    if not _is_storable_mailbox_id(mailbox_id):
        return None, _build_error("Mailbox id is not stable enough for secret storage.")

    config = _resolve_durable_store_config()
    if not config:
        return None, _build_error("Mailbox secret storage is not configured.")

    store_key = build_mailbox_secret_key(owner_email, mailbox_id)
    existing_record, read_error = _read_durable_record(config, store_key)
    if read_error:
        return None, read_error

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    next_record = _normalize_secret_record(existing_record, mailbox_id) or {
        "v": MAILBOX_SECRET_SCHEMA_VERSION,
        "mailboxId": _normalize_mailbox_id(mailbox_id),
        "updatedAt": now,
        "imapPassword": "",
        "smtpPassword": "",
    }

    if isinstance(imap_password, str) and imap_password:
        next_record["imapPassword"] = imap_password

    if isinstance(smtp_password, str) and smtp_password:
        next_record["smtpPassword"] = smtp_password

    next_record["updatedAt"] = now

    _, write_error = _write_durable_record(config, store_key, next_record)
    if write_error:
        return None, write_error

    return next_record, None
