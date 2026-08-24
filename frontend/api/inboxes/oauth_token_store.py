import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.oauth_token_store"
_LEGACY_MODULE_NAME = "oauth_token_store"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "OAuth token-store helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy OAuth token-store identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import json
    import os
    import re
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    MICROSOFT_OAUTH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
    GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    MAX_OAUTH_RESPONSE_BYTES = 256 * 1024
    GOOGLE_CREDENTIAL_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
    GOOGLE_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    GOOGLE_TOKEN_RECORD_FIELDS = frozenset(
        {
            "provider",
            "email",
            "owner_email",
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
    GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX = "cuevion-google-token-raw:v1:"
    GOOGLE_TOKEN_READ_EXACT_SCRIPT = (
        "local current=redis.call('GET',KEYS[1]);"
        "if not current then return false end;"
        f"return '{GOOGLE_TOKEN_RAW_SNAPSHOT_PREFIX}'..current"
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
    GOOGLE_TOKEN_PERSIST_IF_UNCHANGED_SCRIPT = (
        "local current=redis.call('GET',KEYS[1]);"
        "if current~=ARGV[1] then return -1 end;"
        "redis.call('PERSIST',KEYS[1]);"
        "return 1"
    )


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


    def _build_microsoft_store_key(state_or_mailbox_id: str) -> str:
        return f"cuevion:microsoft:oauthtoken:{state_or_mailbox_id.strip().lower()}"


    def _is_valid_google_credential_generation(value: object) -> bool:
        return (
            isinstance(value, str)
            and GOOGLE_CREDENTIAL_GENERATION_PATTERN.fullmatch(value) is not None
        )


    def _is_canonical_google_email(value: object) -> bool:
        return (
            isinstance(value, str)
            and value == value.strip().lower()
            and GOOGLE_EMAIL_PATTERN.fullmatch(value) is not None
        )


    def _is_supported_google_token_timestamp(value: object) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


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
            (not isinstance(scope, str) or not scope.strip())
            and isinstance(existing_record, dict)
            and isinstance(existing_record.get("scope"), str)
        ):
            scope = existing_record["scope"]
        token_type = token_payload.get("token_type")
        now = datetime.now(timezone.utc).isoformat()

        resolved_credential_generation = credential_generation
        if (
            resolved_credential_generation is None
            and isinstance(existing_record, dict)
            and "credential_generation" in existing_record
        ):
            resolved_credential_generation = existing_record.get(
                "credential_generation"
            )
        if (
            resolved_credential_generation is not None
            and not _is_valid_google_credential_generation(
                resolved_credential_generation
            )
        ):
            raise ValueError("Google credential generation is invalid.")

        return {
            "provider": "google",
            "email": email,
            "owner_email": owner_email.strip().lower(),
            **(
                {"credential_generation": resolved_credential_generation}
                if resolved_credential_generation is not None
                else {}
            ),
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


    def build_microsoft_token_record(
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
            "provider": "microsoft",
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
                raw_payload = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
                if len(raw_payload) > MAX_OAUTH_RESPONSE_BYTES:
                    return None, {
                        "code": "gmail_token_store_unavailable",
                        "message": "Durable mailbox token storage returned an invalid response.",
                    }
                if not raw_payload:
                    return None, {
                        "code": "gmail_token_store_unavailable",
                        "message": "Durable mailbox token storage returned an invalid response.",
                    }
                payload = json.loads(raw_payload.decode("utf-8"))
                if not isinstance(payload, dict):
                    return None, {
                        "code": "gmail_token_store_unavailable",
                        "message": "Durable mailbox token storage returned an invalid response.",
                    }
                return payload, None
        except HTTPError:
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Durable mailbox token storage is temporarily unavailable.",
            }
        except (TimeoutError, URLError, OSError):
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Durable mailbox token storage is temporarily unavailable.",
            }
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Durable mailbox token storage returned an invalid response.",
            }
        except Exception:
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Durable mailbox token storage is temporarily unavailable.",
            }


    def _decode_durable_record_payload(
        payload: dict | None,
    ) -> tuple[dict | None, str | None, dict | None]:
        unreadable_error = {
            "code": "gmail_token_store_unavailable",
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


    def _read_durable_record(
        config: dict,
        store_key: str,
    ) -> tuple[dict | None, dict | None]:
        record, _serialized_record, error = _read_durable_record_snapshot(
            config,
            store_key,
        )
        return record, error


    def _records_are_type_exact(left: object, right: object) -> bool:
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


    def _clear_google_store_key_expiry(
        config: dict,
        store_key: str,
        expected_serialized_record: str,
    ) -> dict | None:
        payload, error = _perform_rest_request(
            config,
            "POST",
            "",
            json.dumps(
                [
                    "EVAL",
                    GOOGLE_TOKEN_PERSIST_IF_UNCHANGED_SCRIPT,
                    1,
                    store_key,
                    expected_serialized_record,
                ],
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        if error:
            return error
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, int) and not isinstance(result, bool) and result == 1:
            return None
        if isinstance(result, int) and not isinstance(result, bool) and result == -1:
            return {
                "code": "gmail_token_write_conflict",
                "message": "Gmail authorization changed while it was being inspected.",
            }
        return {
            "code": "gmail_token_store_unavailable",
            "message": "Durable mailbox token storage returned an invalid response.",
        }


    def _write_google_durable_record_if_unchanged(
        config: dict,
        store_key: str,
        expected_serialized_record: str | None,
        record: dict,
    ) -> tuple[dict | None, dict | None]:
        next_serialized_record = json.dumps(
            record,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if expected_serialized_record is None:
            command = [
                "EVAL",
                GOOGLE_TOKEN_CREATE_IF_MISSING_SCRIPT,
                1,
                store_key,
                next_serialized_record,
            ]
        else:
            command = [
                "EVAL",
                GOOGLE_TOKEN_REPLACE_IF_UNCHANGED_SCRIPT,
                1,
                store_key,
                expected_serialized_record,
                next_serialized_record,
            ]

        payload, error = _perform_rest_request(
            config,
            "POST",
            "",
            json.dumps(command, separators=(",", ":")).encode("utf-8"),
        )
        if error:
            return None, error
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, int) and not isinstance(result, bool) and result == 0:
            return None, {
                "code": "gmail_token_write_conflict",
                "message": "Gmail authorization changed while it was being refreshed.",
            }
        if not (
            isinstance(result, int)
            and not isinstance(result, bool)
            and result == 1
        ):
            return None, {
                "code": "token_persistence_failed",
                "message": "Durable mailbox token storage did not confirm the write.",
            }

        verified_record, _verified_serialized, verify_error = (
            _read_durable_record_snapshot(config, store_key)
        )
        if verify_error:
            return None, verify_error
        if not _records_are_type_exact(verified_record, record):
            return None, {
                "code": "gmail_token_write_conflict",
                "message": "Gmail authorization changed while it was being refreshed.",
            }
        return verified_record, None


    def _write_microsoft_durable_record(
        config: dict,
        store_key: str,
        record: dict,
    ) -> tuple[dict | None, dict | None]:
        payload, error = _perform_rest_request(
            config,
            "POST",
            f"/set/{quote(store_key, safe='')}?EX={MICROSOFT_OAUTH_TOKEN_TTL_SECONDS}",
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


    def _load_existing_google_record(
        normalized_email: str,
        *,
        require_durable: bool = False,
    ) -> tuple[str, dict | None, dict | None, str | None, dict | None]:
        store_key = _build_store_key(normalized_email)
        durable_config = _resolve_durable_store_config()

        if durable_config:
            existing_record, serialized_record, existing_error = (
                _read_durable_record_snapshot(durable_config, store_key)
            )
            if existing_error:
                return store_key, durable_config, None, None, existing_error
            if existing_record is not None and not isinstance(
                serialized_record,
                str,
            ):
                return store_key, durable_config, None, None, {
                    "code": "gmail_token_store_unavailable",
                    "message": "Durable mailbox token storage did not provide an exact record snapshot.",
                }
            return (
                store_key,
                durable_config,
                existing_record,
                serialized_record,
                None,
            )

        if require_durable:
            return store_key, None, None, None, {
                "code": "gmail_token_store_unavailable",
                "message": "Gmail authorization storage is temporarily unavailable.",
            }

        existing_store = _read_runtime_store(_resolve_runtime_store_path())
        return store_key, None, existing_store.get(store_key), None, None


    def _google_record_matches_authority(
        record: object,
        *,
        normalized_email: str,
        normalized_owner_email: str,
    ) -> bool:
        if not isinstance(record, dict):
            return False
        record_fields = frozenset(record)
        if record_fields not in {
            GOOGLE_TOKEN_RECORD_FIELDS,
            frozenset({*GOOGLE_TOKEN_RECORD_FIELDS, "credential_generation"}),
        }:
            return False
        credential_generation = record.get("credential_generation")
        expires_at = record.get("expires_at")
        expires_in = record.get("expires_in")
        expiry_is_supported = (
            expires_at is None
            and expires_in is None
            or _is_supported_google_token_timestamp(expires_at)
            and isinstance(expires_in, int)
            and not isinstance(expires_in, bool)
            and expires_in > 0
        )
        return (
            record.get("provider") == "google"
            and record.get("email") == normalized_email
            and record.get("owner_email") == normalized_owner_email
            and _is_canonical_google_email(record.get("email"))
            and _is_canonical_google_email(record.get("owner_email"))
            and isinstance(record.get("access_token"), str)
            and bool(record["access_token"].strip())
            and isinstance(record.get("refresh_token"), str)
            and bool(record["refresh_token"].strip())
            and (
                record.get("token_type") is None
                or isinstance(record.get("token_type"), str)
            )
            and (
                record.get("scope") is None
                or isinstance(record.get("scope"), str)
            )
            and expiry_is_supported
            and _is_supported_google_token_timestamp(record.get("created_at"))
            and _is_supported_google_token_timestamp(record.get("updated_at"))
            and (
                "credential_generation" not in record
                or _is_valid_google_credential_generation(credential_generation)
            )
        )


    def _persist_google_record(
        *,
        normalized_email: str,
        store_key: str,
        durable_config: dict | None,
        expected_serialized_record: str | None,
        record: dict,
        accept_valid_winner_on_conflict: bool = False,
    ) -> tuple[dict | None, dict | None]:
        if durable_config:
            persisted_record, error = _write_google_durable_record_if_unchanged(
                durable_config,
                store_key,
                expected_serialized_record,
                record,
            )
            storage_backend = durable_config["backend"]
            storage_durable = True
        else:
            persisted_record, error = _persist_runtime_record(store_key, record)
            storage_backend = "runtime_tmp_file"
            storage_durable = False

        if error:
            if (
                error.get("code") != "gmail_token_write_conflict"
                or not durable_config
                or not accept_valid_winner_on_conflict
            ):
                return None, error
            persisted_record, winner_serialized, winner_error = (
                _read_durable_record_snapshot(durable_config, store_key)
            )
            if winner_error:
                return None, winner_error
            expected_owner_email = record.get("owner_email")
            if not isinstance(expected_owner_email, str) or not (
                _google_record_matches_authority(
                    persisted_record,
                    normalized_email=normalized_email,
                    normalized_owner_email=expected_owner_email,
                )
            ):
                return None, error
            if isinstance(winner_serialized, str):
                expiry_error = _clear_google_store_key_expiry(
                    durable_config,
                    store_key,
                    winner_serialized,
                )
                if expiry_error:
                    return None, expiry_error

        if not isinstance(persisted_record, dict):
            return None, {
                "code": "token_persistence_failed",
                "message": "Google authentication succeeded, but mailbox token storage could not be verified.",
            }

        expected_owner_email = record.get("owner_email")
        if not isinstance(expected_owner_email, str) or not (
            _google_record_matches_authority(
                persisted_record,
                normalized_email=normalized_email,
                normalized_owner_email=expected_owner_email,
            )
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


    def _exchange_google_refresh_token(
        *,
        refresh_token: str,
    ) -> tuple[dict | None, dict | None]:
        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

        if not google_client_id or not google_client_secret:
            return None, {
                "code": "gmail_refresh_not_configured",
                "message": "Google OAuth refresh is not fully configured.",
            }

        request_payload = urlencode(
            {
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
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
                raw_payload = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
                if len(raw_payload) > MAX_OAUTH_RESPONSE_BYTES:
                    return None, {
                        "code": "gmail_refresh_unavailable",
                        "message": "Google returned an invalid refresh response.",
                    }
                try:
                    payload = (
                        json.loads(raw_payload.decode("utf-8"))
                        if raw_payload
                        else {}
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None, {
                        "code": "gmail_refresh_unavailable",
                        "message": "Google returned an invalid refresh response.",
                    }
                if not isinstance(payload, dict):
                    return None, {
                        "code": "gmail_refresh_unavailable",
                        "message": "Google returned an invalid refresh response.",
                    }
                return payload, None
        except HTTPError as error:
            try:
                error_body = error.read(MAX_OAUTH_RESPONSE_BYTES + 1)
            except Exception:
                return None, {
                    "code": "gmail_refresh_unavailable",
                    "message": "Google authorization refresh is temporarily unavailable.",
                }
            if len(error_body) > MAX_OAUTH_RESPONSE_BYTES:
                error_body = b""
            try:
                parsed_error = (
                    json.loads(error_body.decode("utf-8"))
                    if error_body
                    else {}
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_error = {}
            provider_error = (
                parsed_error.get("error")
                if isinstance(parsed_error, dict)
                and isinstance(parsed_error.get("error"), str)
                else ""
            )
            status_code = error.code if isinstance(error.code, int) else 0

            if status_code == 429:
                return None, {
                    "code": "gmail_refresh_rate_limited",
                    "message": "Google authorization refresh is temporarily rate limited.",
                }
            if status_code == 408 or 500 <= status_code <= 599:
                return None, {
                    "code": "gmail_refresh_unavailable",
                    "message": "Google authorization refresh is temporarily unavailable.",
                }
            if 400 <= status_code <= 499 and provider_error == "invalid_grant":
                return None, {
                    "code": "gmail_refresh_invalid_grant",
                    "message": "Google authorization is no longer reusable.",
                }
            if provider_error in {"invalid_client", "unauthorized_client"}:
                return None, {
                    "code": "gmail_refresh_not_configured",
                    "message": "Google OAuth refresh is not fully configured.",
                }
            return None, {
                "code": "gmail_refresh_failed",
                "message": "Google rejected the authorization refresh request.",
            }
        except (URLError, TimeoutError, OSError):
            return None, {
                "code": "gmail_refresh_unavailable",
                "message": "Google authorization refresh is temporarily unavailable.",
            }
        except Exception:
            return None, {
                "code": "gmail_refresh_unavailable",
                "message": "Google authorization refresh is temporarily unavailable.",
            }


    def persist_google_token_record(
        *,
        email: str,
        owner_email: str,
        token_payload: dict,
        credential_generation: str | None = None,
    ) -> tuple[dict | None, dict | None]:
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return None, {
                "code": "invalid_token_payload",
                "message": "Google returned an incomplete token response.",
            }

        normalized_email = email.strip().lower()
        normalized_owner_email = owner_email.strip().lower()
        if not normalized_owner_email:
            return None, {
                "code": "invalid_token_owner",
                "message": "Authenticated Gmail token ownership is required.",
            }
        if (
            credential_generation is not None
            and not _is_valid_google_credential_generation(credential_generation)
        ):
            return None, {
                "code": "invalid_credential_generation",
                "message": "Google credential generation is invalid.",
            }
        (
            store_key,
            durable_config,
            existing_record,
            expected_serialized_record,
            existing_error,
        ) = _load_existing_google_record(
            normalized_email,
        )
        if existing_error:
            return None, existing_error
        if not (
            _is_canonical_google_email(normalized_email)
            and _is_canonical_google_email(normalized_owner_email)
        ):
            return None, {
                "code": "invalid_token_owner",
                "message": "Authenticated Gmail token ownership is invalid.",
            }
        if existing_record is not None and not _google_record_matches_authority(
            existing_record,
            normalized_email=normalized_email,
            normalized_owner_email=normalized_owner_email,
        ):
            return None, {
                "code": "gmail_token_record_malformed",
                "message": "Stored Gmail authorization cannot be reused safely.",
            }

        next_record = build_google_token_record(
            email=normalized_email,
            owner_email=normalized_owner_email,
            token_payload=token_payload,
            existing_record=existing_record if isinstance(existing_record, dict) else None,
            credential_generation=credential_generation,
        )
        if not _google_record_matches_authority(
            next_record,
            normalized_email=normalized_email,
            normalized_owner_email=normalized_owner_email,
        ):
            return None, {
                "code": "invalid_token_payload",
                "message": "Google did not return a durable refresh authorization.",
            }

        return _persist_google_record(
            normalized_email=normalized_email,
            store_key=store_key,
            durable_config=durable_config,
            expected_serialized_record=expected_serialized_record,
            record=next_record,
        )


    def persist_microsoft_token_record(
        *,
        email: str,
        token_payload: dict,
    ) -> tuple[dict | None, dict | None]:
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return None, {
                "code": "invalid_token_payload",
                "message": "Microsoft returned an incomplete token response.",
            }

        normalized_email = email.strip().lower()
        store_key = _build_microsoft_store_key(normalized_email)
        durable_config = _resolve_durable_store_config()
        existing_record = None

        if durable_config:
            existing_record, existing_error = _read_durable_record(durable_config, store_key)
            if existing_error:
                return None, existing_error
        else:
            existing_store = _read_runtime_store(_resolve_runtime_store_path())
            existing_record = existing_store.get(store_key)

        next_record = build_microsoft_token_record(
            email=normalized_email,
            token_payload=token_payload,
            existing_record=existing_record if isinstance(existing_record, dict) else None,
        )

        if durable_config:
            persisted_record, error = _write_microsoft_durable_record(
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
                "message": "Microsoft authentication succeeded, but mailbox token storage could not be verified.",
            }

        if (
            persisted_record.get("provider") != "microsoft"
            or persisted_record.get("email") != normalized_email
            or not isinstance(persisted_record.get("access_token"), str)
            or not persisted_record.get("access_token")
        ):
            return None, {
                "code": "token_persistence_failed",
                "message": "Microsoft authentication succeeded, but the stored mailbox token record is incomplete.",
            }

        return {
            **persisted_record,
            "_storage_backend": storage_backend,
            "_storage_durable": storage_durable,
        }, None


    def refresh_google_token_record(
        email: str,
        *,
        owner_email: str,
    ) -> tuple[dict | None, dict | None]:
        normalized_email = email.strip().lower()
        normalized_owner_email = owner_email.strip().lower()
        (
            store_key,
            durable_config,
            existing_record,
            expected_serialized_record,
            existing_error,
        ) = _load_existing_google_record(
            normalized_email,
            require_durable=True,
        )
        if existing_error:
            return None, existing_error

        if not isinstance(existing_record, dict):
            return None, {
                "code": "gmail_token_missing",
                "message": "No stored Gmail token is available for this mailbox.",
            }

        stored_owner_email = existing_record.get("owner_email")
        if (
            not normalized_owner_email
            or existing_record.get("provider") != "google"
            or existing_record.get("email") != normalized_email
            or not isinstance(stored_owner_email, str)
            or stored_owner_email != normalized_owner_email
        ):
            return None, {
                "code": "gmail_reconnect_required",
                "message": "Gmail authorization must be securely reconnected.",
            }
        if (
            "credential_generation" in existing_record
            and not _is_valid_google_credential_generation(
                existing_record.get("credential_generation")
            )
        ):
            return None, {
                "code": "gmail_token_record_malformed",
                "message": "Stored Gmail authorization metadata is invalid.",
            }

        refresh_token = existing_record.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            return None, {
                "code": "gmail_refresh_token_missing",
                "message": "The stored Gmail token record does not include a refresh token.",
            }

        if isinstance(expected_serialized_record, str):
            expiry_error = _clear_google_store_key_expiry(
                durable_config,
                store_key,
                expected_serialized_record,
            )
            if expiry_error:
                if expiry_error.get("code") != "gmail_token_write_conflict":
                    return None, expiry_error
                winner, _winner_serialized, winner_error = (
                    _read_durable_record_snapshot(durable_config, store_key)
                )
                if winner_error:
                    return None, winner_error
                if _google_record_matches_authority(
                    winner,
                    normalized_email=normalized_email,
                    normalized_owner_email=normalized_owner_email,
                ):
                    return {
                        **winner,
                        "_storage_backend": durable_config["backend"],
                        "_storage_durable": True,
                    }, None
                return None, {
                    "code": "gmail_reconnect_required",
                    "message": "Gmail authorization changed and must be reconnected.",
                }

        refreshed_payload, refresh_error = _exchange_google_refresh_token(
            refresh_token=refresh_token.strip(),
        )
        if refresh_error:
            return None, refresh_error

        access_token = refreshed_payload.get("access_token") if isinstance(refreshed_payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            return None, {
                "code": "gmail_refresh_failed",
                "message": "Google returned an incomplete refresh token response.",
            }

        next_record = build_google_token_record(
            email=normalized_email,
            owner_email=normalized_owner_email,
            token_payload=refreshed_payload if isinstance(refreshed_payload, dict) else {},
            existing_record=existing_record,
        )

        return _persist_google_record(
            normalized_email=normalized_email,
            store_key=store_key,
            durable_config=durable_config,
            expected_serialized_record=expected_serialized_record,
            record=next_record,
            accept_valid_winner_on_conflict=True,
        )


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


    def load_google_token_record_with_metadata(
        email: str,
        *,
        owner_email: str | None = None,
    ) -> tuple[dict | None, dict | None]:
        """Load one durable Gmail token without hiding store unavailability."""
        normalized_email = email.strip().lower()
        normalized_owner_email = (
            owner_email.strip().lower()
            if isinstance(owner_email, str)
            else ""
        )
        store_key = _build_store_key(normalized_email)
        durable_config = _resolve_durable_store_config()

        if not durable_config:
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Gmail authorization storage is temporarily unavailable.",
            }

        record, serialized_record, error = _read_durable_record_snapshot(
            durable_config,
            store_key,
        )
        if error:
            return None, {
                "code": "gmail_token_store_unavailable",
                "message": "Gmail authorization storage is temporarily unavailable.",
            }
        if not isinstance(record, dict):
            return None, None
        if (
            "credential_generation" in record
            and not _is_valid_google_credential_generation(
                record.get("credential_generation")
            )
        ):
            return None, {
                "code": "gmail_token_record_malformed",
                "message": "Stored Gmail authorization metadata is invalid.",
            }
        if normalized_owner_email and _google_record_matches_authority(
            record,
            normalized_email=normalized_email,
            normalized_owner_email=normalized_owner_email,
        ):
            if isinstance(serialized_record, str):
                expiry_error = _clear_google_store_key_expiry(
                    durable_config,
                    store_key,
                    serialized_record,
                )
                if expiry_error:
                    if expiry_error.get("code") != "gmail_token_write_conflict":
                        return None, expiry_error
                    record, serialized_record, reload_error = (
                        _read_durable_record_snapshot(durable_config, store_key)
                    )
                    if reload_error:
                        return None, reload_error
                    if not isinstance(record, dict):
                        return None, None
        if normalized_owner_email and (
            record.get("provider") == "google"
            and record.get("email") == normalized_email
            and record.get("owner_email") == normalized_owner_email
            and not _google_record_matches_authority(
                record,
                normalized_email=normalized_email,
                normalized_owner_email=normalized_owner_email,
            )
        ):
            return None, {
                "code": "gmail_token_record_malformed",
                "message": "Stored Gmail authorization metadata is invalid.",
            }
        return {
            **record,
            "_storage_backend": durable_config["backend"],
            "_storage_durable": True,
        }, None
