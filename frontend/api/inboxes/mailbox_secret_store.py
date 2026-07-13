from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import normalize_auth_email  # noqa: E402

MAILBOX_SECRET_SCHEMA_VERSION = 1
MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION = 2
MAILBOX_SECRET_KEY_PREFIX = "cuevion:mailbox-secret:v1"
MAILBOX_SECRET_ENCRYPTED_KEY_PREFIX = "cuevion:mailbox-secret:v2"
MAILBOX_SECRET_ALGORITHM = "AES-256-GCM"
MAILBOX_SECRET_NONCE_BYTES = 12
MAILBOX_SECRET_ENCRYPTION_KEY_ENV = "MAILBOX_SECRET_ENCRYPTION_KEY"


class MailboxSecretReadResult(TypedDict):
    status: Literal["present", "missing", "unavailable", "malformed"]
    record: dict | None
    error: dict | None


class EncryptedMailboxSecretSnapshot(TypedDict):
    status: Literal["present", "missing", "unavailable", "malformed"]
    record: dict | None
    error: dict | None


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


def build_encrypted_mailbox_secret_key(owner_email: str, mailbox_id: str) -> str:
    return (
        f"{MAILBOX_SECRET_ENCRYPTED_KEY_PREFIX}:"
        f"{normalize_auth_email(owner_email)}:{_normalize_mailbox_id(mailbox_id)}"
    )


def _build_error(message: str) -> dict:
    return {
        "code": "mailbox_secret_store_unavailable",
        "message": message,
    }


def _build_malformed_error(message: str) -> dict:
    return {
        "code": "mailbox_secret_malformed",
        "message": message,
    }


def _decode_base64url(value: str) -> bytes:
    """Decode canonical URL-safe Base64 with optional required terminal padding."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("invalid base64url value")
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("invalid base64url alphabet")

    unpadded = value.rstrip("=")
    supplied_padding = len(value) - len(unpadded)
    required_padding = (-len(unpadded)) % 4
    if required_padding == 3 or supplied_padding not in {0, required_padding}:
        raise ValueError("invalid base64url padding")

    decoded = base64.urlsafe_b64decode(
        f"{unpadded}{'=' * required_padding}".encode("ascii")
    )
    if _encode_base64url(decoded) != unpadded:
        raise ValueError("non-canonical base64url value")
    return decoded


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _resolve_encryption_key() -> tuple[bytes | None, dict | None]:
    encoded_key = os.getenv(MAILBOX_SECRET_ENCRYPTION_KEY_ENV, "")
    if not encoded_key:
        return None, _build_error("Mailbox secret encryption is not configured.")

    try:
        encryption_key = _decode_base64url(encoded_key)
    except (binascii.Error, ValueError, TypeError):
        return None, _build_error("Mailbox secret encryption configuration is invalid.")

    if len(encryption_key) != 32:
        return None, _build_error("Mailbox secret encryption configuration is invalid.")

    return encryption_key, None


def _build_associated_data(owner_email: str, mailbox_id: str) -> bytes:
    return json.dumps(
        {
            "mailboxId": _normalize_mailbox_id(mailbox_id),
            "ownerEmail": normalize_auth_email(owner_email),
            "v": MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


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
    except json.JSONDecodeError:
        return None, _build_malformed_error(
            "Mailbox secret store returned malformed JSON."
        )
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
            return None, _build_malformed_error(
                "Mailbox secret store returned malformed JSON."
            )
        if not isinstance(parsed, dict):
            return None, _build_malformed_error(
                "Mailbox secret store returned a non-object record."
            )
        return parsed, None

    if not isinstance(result, dict):
        return None, _build_malformed_error(
            "Mailbox secret store returned a non-object record."
        )
    return result, None


def _write_durable_record(config: dict, store_key: str, record: dict) -> tuple[dict | None, dict | None]:
    encoded_record = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _perform_rest_request(
        config,
        "POST",
        f"/set/{quote(store_key, safe='')}",
        body=encoded_record,
    )


def _delete_durable_record(config: dict, store_key: str) -> dict | None:
    _, error = _perform_rest_request(
        config,
        "POST",
        f"/del/{quote(store_key, safe='')}",
    )
    return error


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


def _record_error_status(error: dict | None) -> Literal["unavailable", "malformed"]:
    return "malformed" if error and error.get("code") == "mailbox_secret_malformed" else "unavailable"


def _validate_encrypted_record_shape(record: dict) -> dict | None:
    expected_fields = {"v", "algorithm", "nonce", "ciphertext", "updatedAt"}
    if set(record) != expected_fields:
        return _build_malformed_error("Mailbox secret record is malformed.")
    if (
        type(record.get("v")) is not int
        or record.get("v") != MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION
        or record.get("algorithm") != MAILBOX_SECRET_ALGORITHM
        or not isinstance(record.get("nonce"), str)
        or not isinstance(record.get("ciphertext"), str)
        or not isinstance(record.get("updatedAt"), str)
        or not record.get("updatedAt")
    ):
        return _build_malformed_error("Mailbox secret record is malformed.")
    return None


def _normalize_legacy_secret_record(record: dict, mailbox_id: str) -> tuple[dict | None, dict | None]:
    expected_fields = {
        "v",
        "mailboxId",
        "updatedAt",
        "imapPassword",
        "smtpPassword",
    }
    normalized_mailbox_id = _normalize_mailbox_id(mailbox_id)
    if (
        set(record) != expected_fields
        or type(record.get("v")) is not int
        or record.get("v") != MAILBOX_SECRET_SCHEMA_VERSION
        or not isinstance(record.get("mailboxId"), str)
        or record.get("mailboxId") != normalized_mailbox_id
        or not isinstance(record.get("updatedAt"), str)
        or not record.get("updatedAt")
        or not isinstance(record.get("imapPassword"), str)
        or not isinstance(record.get("smtpPassword"), str)
    ):
        return None, _build_malformed_error(
            "Legacy mailbox secret record is malformed."
        )

    return deepcopy(record), None


def _encrypt_secret_record(
    encryption_key: bytes,
    owner_email: str,
    mailbox_id: str,
    record: dict,
) -> dict:
    normalized_record = _normalize_secret_record(record, mailbox_id)
    if not normalized_record:
        raise ValueError("Mailbox secret record is malformed.")

    plaintext = json.dumps(
        {
            "imapPassword": normalized_record["imapPassword"],
            "smtpPassword": normalized_record["smtpPassword"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    nonce = secrets.token_bytes(MAILBOX_SECRET_NONCE_BYTES)
    ciphertext = AESGCM(encryption_key).encrypt(
        nonce,
        plaintext,
        _build_associated_data(owner_email, mailbox_id),
    )
    return {
        "v": MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION,
        "algorithm": MAILBOX_SECRET_ALGORITHM,
        "nonce": _encode_base64url(nonce),
        "ciphertext": _encode_base64url(ciphertext),
        "updatedAt": normalized_record.get("updatedAt"),
    }


def _decrypt_secret_record(
    encryption_key: bytes,
    owner_email: str,
    mailbox_id: str,
    encrypted_record: dict,
) -> tuple[dict | None, dict | None]:
    if not isinstance(encrypted_record, dict):
        return None, _build_malformed_error("Mailbox secret record is malformed.")
    shape_error = _validate_encrypted_record_shape(encrypted_record)
    if shape_error:
        return None, shape_error

    try:
        nonce = _decode_base64url(encrypted_record["nonce"])
        ciphertext = _decode_base64url(encrypted_record["ciphertext"])
        if len(nonce) != MAILBOX_SECRET_NONCE_BYTES:
            raise ValueError("invalid nonce")
        plaintext = AESGCM(encryption_key).decrypt(
            nonce,
            ciphertext,
            _build_associated_data(owner_email, mailbox_id),
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except (InvalidTag, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None, _build_malformed_error("Mailbox secret record could not be decrypted.")

    if not isinstance(payload, dict) or set(payload) != {"imapPassword", "smtpPassword"}:
        return None, _build_malformed_error("Mailbox secret record is malformed.")

    imap_password = payload.get("imapPassword")
    smtp_password = payload.get("smtpPassword")
    if not isinstance(imap_password, str) or not isinstance(smtp_password, str):
        return None, _build_malformed_error("Mailbox secret record is malformed.")

    return {
        "v": MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION,
        "mailboxId": _normalize_mailbox_id(mailbox_id),
        "updatedAt": encrypted_record.get("updatedAt")
        if isinstance(encrypted_record.get("updatedAt"), str)
        else None,
        "imapPassword": imap_password,
        "smtpPassword": smtp_password,
    }, None


def snapshot_encrypted_mailbox_secret(
    owner_email: str,
    mailbox_id: str,
) -> EncryptedMailboxSecretSnapshot:
    """Capture the exact raw v2 state for trusted compensating rollback."""
    if not _is_storable_mailbox_id(mailbox_id):
        return {
            "status": "malformed",
            "record": None,
            "error": _build_malformed_error("Mailbox id is not valid for secret storage."),
        }

    config = _resolve_durable_store_config()
    if not config:
        return {
            "status": "unavailable",
            "record": None,
            "error": _build_error("Mailbox secret storage is not configured."),
        }

    record, read_error = _read_durable_record(
        config,
        build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
    )
    if read_error:
        return {
            "status": _record_error_status(read_error),
            "record": None,
            "error": read_error,
        }
    if record is None:
        return {"status": "missing", "record": None, "error": None}

    shape_error = _validate_encrypted_record_shape(record)
    if shape_error:
        return {"status": "malformed", "record": None, "error": shape_error}
    return {"status": "present", "record": deepcopy(record), "error": None}


def restore_encrypted_mailbox_secret_snapshot(
    owner_email: str,
    mailbox_id: str,
    snapshot: EncryptedMailboxSecretSnapshot,
) -> dict | None:
    """Restore only the owner/mailbox v2 record captured before a trusted write."""
    if not _is_storable_mailbox_id(mailbox_id):
        return _build_error("Mailbox id is not stable enough for secret rollback.")
    if snapshot.get("status") not in {"present", "missing"}:
        return _build_error("Mailbox secret rollback state is invalid.")

    config = _resolve_durable_store_config()
    if not config:
        return _build_error("Mailbox secret storage is not configured.")

    store_key = build_encrypted_mailbox_secret_key(owner_email, mailbox_id)
    if snapshot["status"] == "missing":
        return _delete_durable_record(config, store_key)

    record = snapshot.get("record")
    if not isinstance(record, dict) or _validate_encrypted_record_shape(record):
        return _build_error("Mailbox secret rollback state is invalid.")
    _, write_error = _write_durable_record(config, store_key, deepcopy(record))
    return write_error


def _write_encrypted_secret_record(
    config: dict,
    encryption_key: bytes,
    owner_email: str,
    mailbox_id: str,
    secret_record: dict,
) -> dict | None:
    encrypted_record = _encrypt_secret_record(
        encryption_key,
        owner_email,
        mailbox_id,
        secret_record,
    )
    _, write_error = _write_durable_record(
        config,
        build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
        encrypted_record,
    )
    return write_error


def read_mailbox_secret(owner_email: str, mailbox_id: str) -> MailboxSecretReadResult:
    if not _is_storable_mailbox_id(mailbox_id):
        return {
            "status": "malformed",
            "record": None,
            "error": _build_malformed_error("Mailbox id is not valid for secret storage."),
        }

    config = _resolve_durable_store_config()
    if not config:
        return {
            "status": "unavailable",
            "record": None,
            "error": _build_error("Mailbox secret storage is not configured."),
        }

    encryption_key, key_error = _resolve_encryption_key()
    if key_error or not encryption_key:
        return {"status": "unavailable", "record": None, "error": key_error}

    encrypted_record, encrypted_error = _read_durable_record(
        config,
        build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
    )
    if encrypted_error:
        return {
            "status": _record_error_status(encrypted_error),
            "record": None,
            "error": encrypted_error,
        }

    if encrypted_record is not None:
        decrypted_record, decrypt_error = _decrypt_secret_record(
            encryption_key,
            owner_email,
            mailbox_id,
            encrypted_record,
        )
        if decrypt_error or not decrypted_record:
            return {"status": "malformed", "record": None, "error": decrypt_error}
        return {"status": "present", "record": decrypted_record, "error": None}

    legacy_record, legacy_error = _read_durable_record(
        config,
        build_mailbox_secret_key(owner_email, mailbox_id),
    )
    if legacy_error:
        return {
            "status": _record_error_status(legacy_error),
            "record": None,
            "error": legacy_error,
        }
    if legacy_record is None:
        return {"status": "missing", "record": None, "error": None}

    normalized_legacy, legacy_shape_error = _normalize_legacy_secret_record(
        legacy_record,
        mailbox_id,
    )
    if legacy_shape_error or not normalized_legacy:
        return {"status": "malformed", "record": None, "error": legacy_shape_error}

    migration_error = _write_encrypted_secret_record(
        config,
        encryption_key,
        owner_email,
        mailbox_id,
        normalized_legacy,
    )
    if migration_error:
        return {"status": "unavailable", "record": None, "error": migration_error}

    return {"status": "present", "record": normalized_legacy, "error": None}


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

    encryption_key, key_error = _resolve_encryption_key()
    if key_error or not encryption_key:
        return None, key_error

    existing_result = read_mailbox_secret(owner_email, mailbox_id)
    if existing_result["status"] in {"unavailable", "malformed"}:
        return None, existing_result["error"]
    existing_record = existing_result["record"]

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

    write_error = _write_encrypted_secret_record(
        config,
        encryption_key,
        owner_email,
        mailbox_id,
        next_record,
    )
    if write_error:
        return None, write_error

    return next_record, None
