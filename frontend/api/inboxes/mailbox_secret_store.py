from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.mailbox_secret_store"
_LEGACY_MODULE_NAME = "mailbox_secret_store"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "Mailbox-store helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy mailbox-store identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import base64
    import binascii
    import json
    import os
    import re
    import secrets
    from copy import deepcopy
    from datetime import datetime, timezone
    from typing import Literal, TypedDict
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from ..auth.email_address import normalize_auth_email

    MAILBOX_SECRET_SCHEMA_VERSION = 1
    MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION = 2
    MAILBOX_SECRET_KEY_PREFIX = "cuevion:mailbox-secret:v1"
    MAILBOX_SECRET_ENCRYPTED_KEY_PREFIX = "cuevion:mailbox-secret:v2"
    MAILBOX_SECRET_ALGORITHM = "AES-256-GCM"
    MAILBOX_SECRET_NONCE_BYTES = 12
    MAILBOX_SECRET_ENCRYPTION_KEY_ENV = "MAILBOX_SECRET_ENCRYPTION_KEY"
    MAILBOX_CREDENTIAL_VERSION_BYTES = 32
    MAILBOX_CREDENTIAL_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


    class MailboxSecretReadResult(TypedDict):
        status: Literal["present", "missing", "unavailable", "malformed"]
        record: dict | None
        error: dict | None


    class EncryptedMailboxSecretSnapshot(TypedDict):
        status: Literal["present", "missing", "unavailable", "malformed"]
        record: dict | None
        error: dict | None


    class MailboxSecretNamespaceSnapshot(TypedDict):
        status: Literal["present", "missing", "unavailable", "malformed"]
        record: None
        error: dict | None


    class MailboxSecretMutationResult(TypedDict):
        status: Literal[
            "applied",
            "not_applied",
            "conflict",
            "ambiguous",
            "malformed",
        ]
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


    def _build_conflict_error(message: str) -> dict:
        return {
            "code": "mailbox_secret_write_conflict",
            "message": message,
        }


    def _build_ambiguous_error(message: str) -> dict:
        return {
            "code": "mailbox_secret_write_ambiguous",
            "message": message,
        }


    def generate_mailbox_credential_version() -> str:
        """Mint an unpredictable identifier for one server-side credential write."""
        return secrets.token_urlsafe(MAILBOX_CREDENTIAL_VERSION_BYTES)


    def is_valid_mailbox_credential_version(value: object) -> bool:
        if not isinstance(value, str) or not MAILBOX_CREDENTIAL_VERSION_PATTERN.fullmatch(
            value
        ):
            return False
        try:
            decoded = _decode_base64url(value)
        except (TypeError, ValueError, binascii.Error):
            return False
        return (
            len(decoded) == MAILBOX_CREDENTIAL_VERSION_BYTES
            and _encode_base64url(decoded) == value
        )


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
        try:
            request = Request(
                f"{config['rest_url']}{path}",
                data=body,
                headers={
                    "Authorization": f"Bearer {config['rest_token']}",
                    "Content-Type": "application/json",
                },
                method=method,
            )
            with urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}, None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, _build_malformed_error(
                "Mailbox secret store returned malformed JSON."
            )
        except HTTPError as error:
            message = f"Mailbox secret store request failed with HTTP {error.code}."
            try:
                error_body = error.read().decode("utf-8", errors="replace")
                parsed_error = json.loads(error_body) if error_body else {}
                if isinstance(parsed_error, dict):
                    candidate = parsed_error.get("error") or parsed_error.get(
                        "message"
                    )
                    if isinstance(candidate, str) and candidate:
                        message = candidate
            except Exception:
                pass
            return None, _build_error(message)
        except (TimeoutError, URLError, OSError) as error:
            return None, _build_error(
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the mailbox secret store.",
            )
        except Exception:
            return None, _build_error("Could not reach the mailbox secret store.")


    def _read_durable_record(config: dict, store_key: str) -> tuple[dict | None, dict | None]:
        payload, error = _perform_rest_request(
            config,
            "GET",
            f"/get/{quote(store_key, safe='')}",
        )
        if error:
            return None, error

        if not isinstance(payload, dict) or set(payload) != {"result"}:
            return None, _build_malformed_error(
                "Mailbox secret store returned an unreadable response."
            )

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
        payload, error = _perform_rest_request(
            config,
            "POST",
            f"/set/{quote(store_key, safe='')}",
            body=encoded_record,
        )
        if error:
            return None, error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"result"}
            or payload.get("result") != "OK"
        ):
            return None, _build_malformed_error(
                "Mailbox secret store did not confirm the write."
            )
        return payload, None


    def _delete_durable_record(config: dict, store_key: str) -> dict | None:
        payload, error = _perform_rest_request(
            config,
            "POST",
            f"/del/{quote(store_key, safe='')}",
        )
        if error:
            return error
        result = payload.get("result") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"result"}
            or type(result) is not int
            or result not in {0, 1}
        ):
            return _build_malformed_error(
                "Mailbox secret store did not confirm the deletion."
            )
        return None


    _CREATE_SECRET_NAMESPACE_IF_MISSING_LUA = r"""
    if redis.call('GET', KEYS[1]) or redis.call('GET', KEYS[2]) then
      return 0
    end
    redis.call('SET', KEYS[1], ARGV[1])
    return 1
    """.strip()

    _COMPARE_AND_SET_SECRET_LUA = r"""
    local current = redis.call('GET', KEYS[1])
    if not current or current ~= ARGV[1] then return 0 end
    redis.call('SET', KEYS[1], ARGV[2])
    return 1
    """.strip()

    _COMPARE_AND_DELETE_SECRET_LUA = r"""
    local current = redis.call('GET', KEYS[1])
    if not current or current ~= ARGV[1] then return 0 end
    redis.call('DEL', KEYS[1])
    return 1
    """.strip()


    def _canonical_record_wire(record: dict) -> str:
        return json.dumps(
            record,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


    def _perform_atomic_secret_command(
        config: dict,
        command: list,
    ) -> tuple[int | None, dict | None]:
        payload, error = _perform_rest_request(
            config,
            "POST",
            "",
            body=json.dumps(command, separators=(",", ":")).encode("utf-8"),
        )
        if error:
            return None, error
        result = payload.get("result") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"result"}
            or type(result) is not int
            or result not in {0, 1}
        ):
            return None, _build_malformed_error(
                "Mailbox secret store did not return an exact conditional acknowledgement."
            )
        return result, None


    def _perform_create_secret_namespace_if_missing(
        config: dict,
        encrypted_store_key: str,
        legacy_store_key: str,
        replacement_record: dict,
    ) -> tuple[int | None, dict | None]:
        return _perform_atomic_secret_command(
            config,
            [
                "EVAL",
                _CREATE_SECRET_NAMESPACE_IF_MISSING_LUA,
                2,
                encrypted_store_key,
                legacy_store_key,
                _canonical_record_wire(replacement_record),
            ],
        )


    def _perform_compare_and_set_secret(
        config: dict,
        store_key: str,
        expected_snapshot: EncryptedMailboxSecretSnapshot,
        replacement_record: dict,
    ) -> tuple[int | None, dict | None]:
        expected_status = expected_snapshot.get("status")
        expected_record = expected_snapshot.get("record")
        if expected_status == "present" and isinstance(expected_record, dict):
            expected_wire = _canonical_record_wire(expected_record)
        else:
            return None, _build_malformed_error(
                "Mailbox secret conditional write state is invalid."
            )
        return _perform_atomic_secret_command(
            config,
            [
                "EVAL",
                _COMPARE_AND_SET_SECRET_LUA,
                1,
                store_key,
                expected_wire,
                _canonical_record_wire(replacement_record),
            ],
        )


    def _perform_compare_and_delete_secret(
        config: dict,
        store_key: str,
        expected_record: dict,
    ) -> tuple[int | None, dict | None]:
        return _perform_atomic_secret_command(
            config,
            [
                "EVAL",
                _COMPARE_AND_DELETE_SECRET_LUA,
                1,
                store_key,
                _canonical_record_wire(expected_record),
            ],
        )


    def _attempt_atomic_secret_mutation(
        operation,
        *args,
    ) -> tuple[int | None, dict | None]:
        """Turn operational exceptions into an unknown ACK that callers must read back."""
        try:
            return operation(*args)
        except Exception:
            return None, _build_error(
                "Mailbox secret store did not return a conditional acknowledgement."
            )


    def _normalize_secret_record(record: dict | None, mailbox_id: str) -> dict | None:
        if not isinstance(record, dict):
            return None

        normalized_mailbox_id = _normalize_mailbox_id(mailbox_id)
        imap_password = record.get("imapPassword")
        smtp_password = record.get("smtpPassword")
        credential_version = record.get("credentialVersion")

        normalized = {
            "v": MAILBOX_SECRET_SCHEMA_VERSION,
            "mailboxId": normalized_mailbox_id,
            "updatedAt": record.get("updatedAt") if isinstance(record.get("updatedAt"), str) else None,
            "imapPassword": imap_password if isinstance(imap_password, str) else "",
            "smtpPassword": smtp_password if isinstance(smtp_password, str) else "",
        }
        if is_valid_mailbox_credential_version(credential_version):
            normalized["credentialVersion"] = credential_version
        return normalized


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
        if not normalized_record or not is_valid_mailbox_credential_version(
            normalized_record.get("credentialVersion")
        ):
            raise ValueError("Mailbox secret record is malformed.")

        plaintext = json.dumps(
            {
                "credentialVersion": normalized_record["credentialVersion"],
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

        if not isinstance(payload, dict) or set(payload) not in (
            {"imapPassword", "smtpPassword"},
            {"credentialVersion", "imapPassword", "smtpPassword"},
        ):
            return None, _build_malformed_error("Mailbox secret record is malformed.")

        imap_password = payload.get("imapPassword")
        smtp_password = payload.get("smtpPassword")
        if not isinstance(imap_password, str) or not isinstance(smtp_password, str):
            return None, _build_malformed_error("Mailbox secret record is malformed.")
        credential_version = payload.get("credentialVersion")
        if "credentialVersion" in payload and not is_valid_mailbox_credential_version(
            credential_version
        ):
            return None, _build_malformed_error("Mailbox secret record is malformed.")

        decrypted = {
            "v": MAILBOX_SECRET_ENCRYPTED_SCHEMA_VERSION,
            "mailboxId": _normalize_mailbox_id(mailbox_id),
            "updatedAt": encrypted_record.get("updatedAt")
            if isinstance(encrypted_record.get("updatedAt"), str)
            else None,
            "imapPassword": imap_password,
            "smtpPassword": smtp_password,
        }
        if credential_version is not None:
            decrypted["credentialVersion"] = credential_version
        return decrypted, None


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


    def snapshot_mailbox_secret_namespace(
        owner_email: str,
        mailbox_id: str,
    ) -> MailboxSecretNamespaceSnapshot:
        """Read both secret namespaces without decrypting, migrating, or writing."""
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

        for store_key in (
            build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
            build_mailbox_secret_key(owner_email, mailbox_id),
        ):
            record, read_error = _read_durable_record(config, store_key)
            if read_error:
                return {
                    "status": _record_error_status(read_error),
                    "record": None,
                    "error": read_error,
                }
            if record is not None:
                return {"status": "present", "record": None, "error": None}

        return {"status": "missing", "record": None, "error": None}


    def _mutation_result(
        status: MailboxSecretMutationResult["status"],
        *,
        record: dict | None = None,
        error: dict | None = None,
    ) -> MailboxSecretMutationResult:
        return {
            "status": status,
            "record": deepcopy(record) if isinstance(record, dict) else None,
            "error": deepcopy(error) if isinstance(error, dict) else None,
        }


    def _snapshots_are_exact(
        left: EncryptedMailboxSecretSnapshot,
        right: EncryptedMailboxSecretSnapshot,
    ) -> bool:
        if left.get("status") != right.get("status"):
            return False
        if left.get("status") == "missing":
            return left.get("record") is None and right.get("record") is None
        if left.get("status") != "present":
            return False
        left_record = left.get("record")
        right_record = right.get("record")
        return (
            isinstance(left_record, dict)
            and isinstance(right_record, dict)
            and _canonical_record_wire(left_record)
            == _canonical_record_wire(right_record)
        )


    def _finish_conditional_set(
        owner_email: str,
        mailbox_id: str,
        expected_snapshot: EncryptedMailboxSecretSnapshot,
        intended_encrypted_record: dict,
        intended_secret_record: dict,
        acknowledgement: int | None,
        acknowledgement_error: dict | None,
    ) -> MailboxSecretMutationResult:
        if (
            type(acknowledgement) is int
            and acknowledgement == 1
            and acknowledgement_error is None
        ):
            return _mutation_result("applied", record=intended_secret_record)
        if type(acknowledgement) is int and acknowledgement == 0:
            return _mutation_result(
                "conflict",
                error=_build_conflict_error(
                    "Mailbox credentials changed before the conditional write."
                ),
            )

        readback = snapshot_encrypted_mailbox_secret(owner_email, mailbox_id)
        intended_snapshot: EncryptedMailboxSecretSnapshot = {
            "status": "present",
            "record": intended_encrypted_record,
            "error": None,
        }
        if _snapshots_are_exact(readback, intended_snapshot):
            return _mutation_result("applied", record=intended_secret_record)
        if readback.get("status") not in {"present", "missing"}:
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret write outcome could not be verified."
                ),
            )
        if (
            expected_snapshot.get("status") == "missing"
            and readback.get("status") == "missing"
        ):
            namespace_readback = snapshot_mailbox_secret_namespace(
                owner_email,
                mailbox_id,
            )
            if namespace_readback.get("status") == "missing":
                return _mutation_result(
                    "not_applied",
                    error=acknowledgement_error
                    or _build_error("Mailbox secret write was not confirmed."),
                )
            if namespace_readback.get("status") == "present":
                return _mutation_result(
                    "conflict",
                    error=_build_conflict_error(
                        "Another mailbox secret namespace won the write."
                    ),
                )
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret namespace outcome could not be verified."
                ),
            )
        if _snapshots_are_exact(readback, expected_snapshot):
            return _mutation_result(
                "not_applied",
                error=acknowledgement_error
                or _build_error("Mailbox secret write was not confirmed."),
            )
        return _mutation_result(
            "conflict",
            error=_build_conflict_error(
                "Another mailbox credential generation won the write."
            ),
        )


    def _build_next_secret_record(
        mailbox_id: str,
        credential_version: str,
        existing_record: dict | None,
        imap_password: str | None,
        smtp_password: str | None,
    ) -> dict:
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
        next_record["credentialVersion"] = credential_version
        next_record["updatedAt"] = now
        return next_record


    def create_mailbox_secret_if_missing(
        owner_email: str,
        mailbox_id: str,
        credential_version: str,
        *,
        imap_password: str | None = None,
        smtp_password: str | None = None,
    ) -> MailboxSecretMutationResult:
        """Atomically create a secret only while both secret namespaces are absent."""
        if not _is_storable_mailbox_id(mailbox_id) or not is_valid_mailbox_credential_version(
            credential_version
        ):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error(
                    "Mailbox secret generation or mailbox id is invalid."
                ),
            )
        config = _resolve_durable_store_config()
        if not config:
            return _mutation_result(
                "ambiguous",
                error=_build_error("Mailbox secret storage is not configured."),
            )
        encryption_key, key_error = _resolve_encryption_key()
        if key_error or not encryption_key:
            return _mutation_result("ambiguous", error=key_error)

        intended_secret = _build_next_secret_record(
            mailbox_id,
            credential_version,
            None,
            imap_password,
            smtp_password,
        )
        try:
            intended_encrypted = _encrypt_secret_record(
                encryption_key,
                owner_email,
                mailbox_id,
                intended_secret,
            )
        except Exception:
            return _mutation_result(
                "malformed",
                error=_build_malformed_error("Mailbox secret record is malformed."),
            )

        encrypted_key = build_encrypted_mailbox_secret_key(owner_email, mailbox_id)
        legacy_key = build_mailbox_secret_key(owner_email, mailbox_id)
        acknowledgement, acknowledgement_error = _attempt_atomic_secret_mutation(
            _perform_create_secret_namespace_if_missing,
            config,
            encrypted_key,
            legacy_key,
            intended_encrypted,
        )
        return _finish_conditional_set(
            owner_email,
            mailbox_id,
            {"status": "missing", "record": None, "error": None},
            intended_encrypted,
            intended_secret,
            acknowledgement,
            acknowledgement_error,
        )


    def replace_mailbox_secret_if_unchanged(
        owner_email: str,
        mailbox_id: str,
        expected_snapshot: EncryptedMailboxSecretSnapshot,
        credential_version: str,
        *,
        imap_password: str | None = None,
        smtp_password: str | None = None,
    ) -> MailboxSecretMutationResult:
        """Atomically replace the exact encrypted snapshot with one new generation."""
        if (
            not _is_storable_mailbox_id(mailbox_id)
            or not is_valid_mailbox_credential_version(credential_version)
            or expected_snapshot.get("status") != "present"
        ):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error(
                    "Mailbox secret conditional write state is invalid."
                ),
            )
        expected_record = expected_snapshot.get("record")
        if (
            not isinstance(expected_record, dict)
            or _validate_encrypted_record_shape(expected_record)
        ):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error(
                    "Mailbox secret conditional write state is invalid."
                ),
            )

        config = _resolve_durable_store_config()
        if not config:
            return _mutation_result(
                "ambiguous",
                error=_build_error("Mailbox secret storage is not configured."),
            )
        encryption_key, key_error = _resolve_encryption_key()
        if key_error or not encryption_key:
            return _mutation_result("ambiguous", error=key_error)

        expected_secret, expected_secret_error = _decrypt_secret_record(
            encryption_key,
            owner_email,
            mailbox_id,
            expected_record,
        )
        if expected_secret_error or not expected_secret:
            return _mutation_result(
                "malformed",
                error=expected_secret_error
                or _build_malformed_error(
                    "Mailbox secret conditional write state is invalid."
                ),
            )
        intended_secret = _build_next_secret_record(
            mailbox_id,
            credential_version,
            expected_secret,
            imap_password,
            smtp_password,
        )
        try:
            intended_encrypted = _encrypt_secret_record(
                encryption_key,
                owner_email,
                mailbox_id,
                intended_secret,
            )
        except Exception:
            return _mutation_result(
                "malformed",
                error=_build_malformed_error("Mailbox secret record is malformed."),
            )

        acknowledgement, acknowledgement_error = _attempt_atomic_secret_mutation(
            _perform_compare_and_set_secret,
            config,
            build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
            expected_snapshot,
            intended_encrypted,
        )
        return _finish_conditional_set(
            owner_email,
            mailbox_id,
            expected_snapshot,
            intended_encrypted,
            intended_secret,
            acknowledgement,
            acknowledgement_error,
        )


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

        # Legacy reads are deliberately side-effect free. They remain generationless
        # and therefore unusable until an authenticated reconnect performs a CAS
        # migration of both the config and encrypted secret.
        return {"status": "present", "record": normalized_legacy, "error": None}


    def _read_current_generation_snapshot(
        owner_email: str,
        mailbox_id: str,
        expected_credential_version: str,
    ) -> tuple[EncryptedMailboxSecretSnapshot | None, MailboxSecretMutationResult | None]:
        if not is_valid_mailbox_credential_version(expected_credential_version):
            return None, _mutation_result(
                "malformed",
                error=_build_malformed_error(
                    "Mailbox secret cleanup generation is invalid."
                ),
            )
        snapshot = snapshot_encrypted_mailbox_secret(owner_email, mailbox_id)
        if snapshot["status"] == "missing":
            return None, _mutation_result(
                "conflict",
                error=_build_conflict_error(
                    "Mailbox credential generation is no longer current."
                ),
            )
        if snapshot["status"] not in {"present"} or not isinstance(
            snapshot.get("record"), dict
        ):
            return None, _mutation_result(
                "ambiguous"
                if snapshot["status"] == "unavailable"
                else "malformed",
                error=snapshot.get("error")
                or _build_ambiguous_error(
                    "Mailbox secret cleanup state could not be verified."
                ),
            )

        encryption_key, key_error = _resolve_encryption_key()
        if key_error or not encryption_key:
            return None, _mutation_result("ambiguous", error=key_error)
        decrypted, decrypt_error = _decrypt_secret_record(
            encryption_key,
            owner_email,
            mailbox_id,
            snapshot["record"],
        )
        if decrypt_error or not decrypted:
            return None, _mutation_result("malformed", error=decrypt_error)
        if decrypted.get("credentialVersion") != expected_credential_version:
            return None, _mutation_result(
                "conflict",
                error=_build_conflict_error(
                    "Another mailbox credential generation is current."
                ),
            )
        return snapshot, None


    def delete_mailbox_secret_if_current_generation(
        owner_email: str,
        mailbox_id: str,
        expected_credential_version: str,
    ) -> MailboxSecretMutationResult:
        """Delete only the exact raw secret that still decrypts to the expected generation."""
        if not _is_storable_mailbox_id(mailbox_id):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error("Mailbox id is invalid for secret cleanup."),
            )
        current, current_error = _read_current_generation_snapshot(
            owner_email,
            mailbox_id,
            expected_credential_version,
        )
        if current_error or not current or not isinstance(current.get("record"), dict):
            return current_error or _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret cleanup state could not be verified."
                ),
            )
        config = _resolve_durable_store_config()
        if not config:
            return _mutation_result(
                "ambiguous",
                error=_build_error("Mailbox secret storage is not configured."),
            )
        acknowledgement, acknowledgement_error = _attempt_atomic_secret_mutation(
            _perform_compare_and_delete_secret,
            config,
            build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
            current["record"],
        )
        if (
            type(acknowledgement) is int
            and acknowledgement == 1
            and acknowledgement_error is None
        ):
            return _mutation_result("applied")
        if type(acknowledgement) is int and acknowledgement == 0:
            return _mutation_result(
                "conflict",
                error=_build_conflict_error(
                    "Mailbox credential generation changed before deletion."
                ),
            )

        readback = snapshot_encrypted_mailbox_secret(owner_email, mailbox_id)
        if readback["status"] == "missing":
            namespace_readback = snapshot_mailbox_secret_namespace(
                owner_email,
                mailbox_id,
            )
            if namespace_readback.get("status") == "missing":
                return _mutation_result("applied")
            if namespace_readback.get("status") == "present":
                return _mutation_result(
                    "conflict",
                    error=_build_conflict_error(
                        "A concurrent mailbox secret namespace was preserved."
                    ),
                )
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret deletion outcome could not be verified."
                ),
            )
        if readback["status"] not in {"present"}:
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret deletion outcome could not be verified."
                ),
            )
        if _snapshots_are_exact(readback, current):
            return _mutation_result(
                "not_applied",
                error=acknowledgement_error
                or _build_error("Mailbox secret deletion was not confirmed."),
            )
        return _mutation_result(
            "conflict",
            error=_build_conflict_error(
                "A newer mailbox credential generation was preserved."
            ),
        )


    def restore_mailbox_secret_if_current_generation(
        owner_email: str,
        mailbox_id: str,
        expected_credential_version: str,
        previous_snapshot: EncryptedMailboxSecretSnapshot,
    ) -> MailboxSecretMutationResult:
        """Restore an exact prior snapshot only while this request's generation is current."""
        if (
            not _is_storable_mailbox_id(mailbox_id)
            or previous_snapshot.get("status") not in {"present", "missing"}
        ):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error("Mailbox secret rollback state is invalid."),
            )
        previous_record = previous_snapshot.get("record")
        if previous_snapshot["status"] == "present" and (
            not isinstance(previous_record, dict)
            or _validate_encrypted_record_shape(previous_record)
        ):
            return _mutation_result(
                "malformed",
                error=_build_malformed_error("Mailbox secret rollback state is invalid."),
            )

        current, current_error = _read_current_generation_snapshot(
            owner_email,
            mailbox_id,
            expected_credential_version,
        )
        if current_error or not current or not isinstance(current.get("record"), dict):
            return current_error or _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret rollback state could not be verified."
                ),
            )
        config = _resolve_durable_store_config()
        if not config:
            return _mutation_result(
                "ambiguous",
                error=_build_error("Mailbox secret storage is not configured."),
            )

        if previous_snapshot["status"] == "missing":
            acknowledgement, acknowledgement_error = _attempt_atomic_secret_mutation(
                _perform_compare_and_delete_secret,
                config,
                build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
                current["record"],
            )
        else:
            acknowledgement, acknowledgement_error = _attempt_atomic_secret_mutation(
                _perform_compare_and_set_secret,
                config,
                build_encrypted_mailbox_secret_key(owner_email, mailbox_id),
                current,
                previous_record,
            )
        if (
            type(acknowledgement) is int
            and acknowledgement == 1
            and acknowledgement_error is None
        ):
            return _mutation_result("applied")
        if type(acknowledgement) is int and acknowledgement == 0:
            return _mutation_result(
                "conflict",
                error=_build_conflict_error(
                    "Mailbox credential generation changed before rollback."
                ),
            )

        readback = snapshot_encrypted_mailbox_secret(owner_email, mailbox_id)
        if (
            previous_snapshot.get("status") == "missing"
            and readback.get("status") == "missing"
        ):
            namespace_readback = snapshot_mailbox_secret_namespace(
                owner_email,
                mailbox_id,
            )
            if namespace_readback.get("status") == "missing":
                return _mutation_result("applied")
            if namespace_readback.get("status") == "present":
                return _mutation_result(
                    "conflict",
                    error=_build_conflict_error(
                        "A concurrent mailbox secret namespace was preserved."
                    ),
                )
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret rollback outcome could not be verified."
                ),
            )
        if _snapshots_are_exact(readback, previous_snapshot):
            return _mutation_result("applied")
        if readback["status"] not in {"present", "missing"}:
            return _mutation_result(
                "ambiguous",
                error=_build_ambiguous_error(
                    "Mailbox secret rollback outcome could not be verified."
                ),
            )
        if _snapshots_are_exact(readback, current):
            return _mutation_result(
                "not_applied",
                error=acknowledgement_error
                or _build_error("Mailbox secret rollback was not confirmed."),
            )
        return _mutation_result(
            "conflict",
            error=_build_conflict_error(
                "A newer mailbox credential generation was preserved."
            ),
        )


    def restore_encrypted_mailbox_secret_snapshot(
        owner_email: str,
        mailbox_id: str,
        snapshot: EncryptedMailboxSecretSnapshot,
        *,
        expected_credential_version: str,
    ) -> dict | None:
        """Compatibility wrapper for generation-bound compensating rollback."""
        result = restore_mailbox_secret_if_current_generation(
            owner_email,
            mailbox_id,
            expected_credential_version,
            snapshot,
        )
        return None if result["status"] == "applied" else result["error"]


    def save_mailbox_secret(
        owner_email: str,
        mailbox_id: str,
        imap_password: str | None = None,
        smtp_password: str | None = None,
        *,
        credential_version: str | None = None,
        expected_snapshot: EncryptedMailboxSecretSnapshot | None = None,
        require_namespace_missing: bool = False,
    ) -> tuple[dict | None, dict | None]:
        """Persist one new generation through an atomic conditional mutation."""
        if not _is_storable_mailbox_id(mailbox_id):
            return None, _build_error("Mailbox id is not stable enough for secret storage.")
        version = credential_version or generate_mailbox_credential_version()
        if not is_valid_mailbox_credential_version(version):
            return None, _build_malformed_error(
                "Mailbox credential generation is invalid."
            )

        if require_namespace_missing:
            mutation = create_mailbox_secret_if_missing(
                owner_email,
                mailbox_id,
                version,
                imap_password=imap_password,
                smtp_password=smtp_password,
            )
        else:
            snapshot = expected_snapshot or snapshot_encrypted_mailbox_secret(
                owner_email,
                mailbox_id,
            )
            if snapshot["status"] not in {"present", "missing"}:
                return None, snapshot.get("error") or _build_error(
                    "Mailbox credential state could not be prepared."
                )
            if snapshot["status"] == "missing":
                mutation = create_mailbox_secret_if_missing(
                    owner_email,
                    mailbox_id,
                    version,
                    imap_password=imap_password,
                    smtp_password=smtp_password,
                )
            else:
                mutation = replace_mailbox_secret_if_unchanged(
                    owner_email,
                    mailbox_id,
                    snapshot,
                    version,
                    imap_password=imap_password,
                    smtp_password=smtp_password,
                )

        if mutation["status"] != "applied":
            return None, mutation["error"] or _build_error(
                "Mailbox credentials could not be stored."
            )
        return mutation["record"], None
