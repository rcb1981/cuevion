from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.collaboration.redis_store"
_LEGACY_MODULE_NAME = "redis_store"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "Collaboration helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy store identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import json
    import hashlib
    import base64
    import hmac
    import os
    import re
    import secrets
    import unicodedata
    from dataclasses import dataclass
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    from .models import (
        COLLABORATION_THREAD_SCHEMA_VERSION,
        MAX_V2_EXTERNAL_GUESTS,
        MAX_V2_GUEST_SESSION_LIFETIME_SECONDS,
        MAX_V2_INVITE_BYTES,
        MAX_V2_THREAD_BYTES,
        MIN_V2_TIMESTAMP_SECONDS,
        MAX_V2_TIMESTAMP_SECONDS,
        MIN_V2_TIMESTAMP_MILLISECONDS,
        MAX_V2_TIMESTAMP_MILLISECONDS,
        decode_v2_wire_record,
        encode_v2_wire_record,
        hash_v2_secret,
        is_active_collaboration_invite_record,
        normalize_collaboration_invite_record,
        normalize_collaboration_thread_record,
        normalize_v2_email,
        normalize_v2_invite_record,
        normalize_v2_message_record,
        normalize_v2_owner_idempotency_key,
        normalize_v2_source_ref,
        normalize_v2_thread_record,
        normalize_v2_user_id,
        normalize_v2_workspace_id,
    )

    MAX_COLLABORATION_THREAD_BATCH_SIZE = 200
    MAX_PARTICIPANT_THREAD_SCAN_KEYS = 5000


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


    def build_thread_key(workspace_id: str, message_id: str) -> str:
        return f"cuevion:collab:v1:thread:{workspace_id.strip().lower()}:{message_id.strip()}"


    def build_invite_key(token: str) -> str:
        return f"cuevion:collab:v1:invite:{token.strip()}"


    def build_thread_invite_key(workspace_id: str, message_id: str, invitee_email: str) -> str:
        return (
            "cuevion:collab:v1:thread-invite:"
            f"{workspace_id.strip().lower()}:{message_id.strip()}:{invitee_email.strip().lower()}"
        )


    def is_collaboration_store_configured() -> bool:
        return _resolve_durable_store_config() is not None


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
                "code": "collaboration_store_unavailable",
                "message": (
                    parsed_error.get("error")
                    or parsed_error.get("message")
                    or f"Collaboration store request failed with HTTP {error.code}."
                ),
            }
        except URLError as error:
            return None, {
                "code": "collaboration_store_unavailable",
                "message": (
                    str(error.reason)
                    if getattr(error, "reason", None)
                    else "Could not reach the collaboration store."
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

        if not isinstance(payload, dict):
            return None, {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store returned an unreadable response.",
            }

        result = payload.get("result")
        if result is None:
            return None, None

        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return None, {
                    "code": "collaboration_store_unavailable",
                    "message": "Collaboration store returned malformed JSON.",
                }
            return parsed if isinstance(parsed, dict) else None, None

        return result if isinstance(result, dict) else None, None


    def _normalize_scan_response(payload: dict) -> tuple[str, list[str]] | None:
        result = payload.get("result")

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return None

        if isinstance(result, (list, tuple)) and len(result) >= 2:
            cursor = str(result[0] or "0")
            keys_value = result[1]
        elif isinstance(result, dict):
            cursor = str(result.get("cursor") or result.get("nextCursor") or "0")
            keys_value = result.get("keys") or result.get("results") or []
        else:
            return None

        if not isinstance(keys_value, list):
            return None

        keys = [key for key in keys_value if isinstance(key, str) and key.strip()]
        return cursor, keys


    def _scan_durable_keys(
        config: dict,
        *,
        pattern: str,
        max_keys: int = MAX_COLLABORATION_THREAD_BATCH_SIZE,
        scan_count: int = 100,
        max_iterations: int = 20,
    ) -> tuple[list[str], dict | None]:
        cursor = "0"
        keys: list[str] = []
        seen_keys: set[str] = set()

        for _ in range(max_iterations):
            payload, error = _perform_rest_request(
                config,
                "GET",
                (
                    f"/scan/{quote(cursor, safe='')}"
                    f"/match/{quote(pattern, safe='')}"
                    f"/count/{scan_count}"
                ),
            )
            if error:
                return [], error

            if not isinstance(payload, dict):
                return [], {
                    "code": "collaboration_store_unavailable",
                    "message": "Collaboration store returned an unreadable scan response.",
                }

            scan_result = _normalize_scan_response(payload)
            if scan_result is None:
                return [], {
                    "code": "collaboration_store_unavailable",
                    "message": "Collaboration store returned an unreadable scan response.",
                }

            cursor, scanned_keys = scan_result
            for key in scanned_keys:
                if key in seen_keys:
                    continue

                seen_keys.add(key)
                keys.append(key)
                if len(keys) >= max_keys:
                    return keys, None

            if cursor == "0":
                break

        return keys, None


    def get_threads_many(workspace_id: str, message_ids: list[str]) -> dict[str, dict]:
        normalized_workspace_id = workspace_id.strip().lower()
        if not normalized_workspace_id:
            return {}

        deduped_message_ids: list[str] = []
        seen_message_ids: set[str] = set()
        for message_id in message_ids:
            if not isinstance(message_id, str):
                continue

            normalized_message_id = message_id.strip()
            if not normalized_message_id or normalized_message_id in seen_message_ids:
                continue

            seen_message_ids.add(normalized_message_id)
            deduped_message_ids.append(normalized_message_id)

            if len(deduped_message_ids) >= MAX_COLLABORATION_THREAD_BATCH_SIZE:
                break

        if not deduped_message_ids:
            return {}

        config = _resolve_durable_store_config()
        if not config:
            return {}

        threads_by_message_id: dict[str, dict] = {}

        for message_id in deduped_message_ids:
            thread_key = build_thread_key(normalized_workspace_id, message_id)
            record, error = _read_durable_record(config, thread_key)
            if error or not record:
                continue

            normalized_thread = normalize_collaboration_thread_record(record)
            if not normalized_thread:
                continue

            if (
                normalized_thread["workspaceId"] != normalized_workspace_id
                or normalized_thread["messageId"] != message_id
            ):
                continue

            threads_by_message_id[message_id] = normalized_thread

        return threads_by_message_id


    def get_participant_threads(
        participant_email: str,
        workspace_id: str | None = None,
    ) -> tuple[list[dict], dict | None]:
        normalized_participant_email = participant_email.strip().lower()
        normalized_workspace_id = workspace_id.strip().lower() if workspace_id else ""

        if not normalized_participant_email:
            return [], {
                "code": "invalid_request",
                "message": "participantEmail is required.",
            }

        config = _resolve_durable_store_config()
        if not config:
            return [], {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store is not configured.",
            }

        pattern = build_thread_key(normalized_workspace_id or "*", "*")
        thread_keys, error = _scan_durable_keys(
            config,
            pattern=pattern,
            max_keys=MAX_PARTICIPANT_THREAD_SCAN_KEYS,
            scan_count=500,
            max_iterations=40,
        )
        if error:
            return [], error

        threads: list[dict] = []
        seen_thread_keys: set[str] = set()

        for thread_key in thread_keys:
            record, read_error = _read_durable_record(config, thread_key)
            if read_error:
                return [], read_error

            if not record:
                continue

            normalized_thread = normalize_collaboration_thread_record(record)
            if not normalized_thread:
                continue

            if (
                normalized_workspace_id
                and normalized_thread["workspaceId"] != normalized_workspace_id
            ):
                continue

            if normalized_thread["collaboration"]["state"] == "resolved":
                continue

            has_matching_participant = any(
                participant.get("kind") == "internal"
                and participant.get("status") in {"active", "invited"}
                and str(participant.get("email") or "").strip().lower()
                == normalized_participant_email
                for participant in normalized_thread["collaboration"].get("participants", [])
            )
            if not has_matching_participant:
                continue

            stable_thread_key = build_thread_key(
                normalized_thread["workspaceId"],
                normalized_thread["messageId"],
            )
            if stable_thread_key in seen_thread_keys:
                continue

            seen_thread_keys.add(stable_thread_key)
            threads.append(normalized_thread)

        threads.sort(
            key=lambda thread: int(thread["collaboration"].get("updatedAt") or 0),
            reverse=True,
        )
        return threads, None


    def get_thread(workspace_id: str, message_id: str) -> dict | None:
        normalized_workspace_id = workspace_id.strip().lower()
        normalized_message_id = message_id.strip()

        if not normalized_workspace_id or not normalized_message_id:
            return None

        config = _resolve_durable_store_config()
        if not config:
            return None

        record, error = _read_durable_record(
            config,
            build_thread_key(normalized_workspace_id, normalized_message_id),
        )
        if error or not record:
            return None

        normalized_thread = normalize_collaboration_thread_record(record)
        if not normalized_thread:
            return None

        if (
            normalized_thread["workspaceId"] != normalized_workspace_id
            or normalized_thread["messageId"] != normalized_message_id
        ):
            return None

        return normalized_thread


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

        if not isinstance(payload, dict) or payload.get("result") != "OK":
            return None, {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store did not confirm the write.",
            }

        return payload, None


    def save_thread(thread_record: dict) -> tuple[dict | None, dict | None]:
        normalized_thread = normalize_collaboration_thread_record(thread_record)
        if not normalized_thread:
            return None, {
                "code": "invalid_thread",
                "message": "Thread record is invalid.",
            }

        config = _resolve_durable_store_config()
        if not config:
            return None, {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store is not configured.",
            }

        _, error = _write_durable_record(
            config,
            build_thread_key(normalized_thread["workspaceId"], normalized_thread["messageId"]),
            normalized_thread,
        )
        if error:
            return None, error

        return normalized_thread, None


    def create_thread_if_missing(thread_record: dict) -> tuple[dict | None, dict | None]:
        normalized_thread = normalize_collaboration_thread_record(thread_record)
        if not normalized_thread:
            return None, {
                "code": "invalid_thread",
                "message": "Thread record is invalid.",
            }

        existing_thread = get_thread(
            normalized_thread["workspaceId"],
            normalized_thread["messageId"],
        )
        if existing_thread:
            return existing_thread, None

        return save_thread(normalized_thread)


    def save_thread_if_expected(
        thread_record: dict,
        expected_updated_at: int | None = None,
    ) -> tuple[dict | None, dict | None]:
        normalized_thread = normalize_collaboration_thread_record(thread_record)
        if not normalized_thread:
            return None, {
                "code": "invalid_thread",
                "message": "Thread record is invalid.",
            }

        existing_thread = get_thread(
            normalized_thread["workspaceId"],
            normalized_thread["messageId"],
        )
        if not existing_thread:
            return None, {
                "code": "thread_not_found",
                "message": "Canonical collaboration thread was not found.",
            }

        if (
            expected_updated_at is not None
            and existing_thread["collaboration"]["updatedAt"] != expected_updated_at
        ):
            return existing_thread, {
                "code": "stale_thread",
                "message": "Canonical collaboration thread is newer than the local version.",
            }

        return save_thread(normalized_thread)


    def get_invite(token: str) -> dict | None:
        normalized_token = token.strip()
        if not normalized_token:
            return None

        config = _resolve_durable_store_config()
        if not config:
            return None

        record, error = _read_durable_record(
            config,
            build_invite_key(normalized_token),
        )
        if error or not record:
            return None

        normalized_invite = normalize_collaboration_invite_record(record)
        if not normalized_invite or normalized_invite["token"] != normalized_token:
            return None

        return normalized_invite


    def get_thread_invite(workspace_id: str, message_id: str, invitee_email: str) -> dict | None:
        normalized_workspace_id = workspace_id.strip().lower()
        normalized_message_id = message_id.strip()
        normalized_invitee_email = invitee_email.strip().lower()

        if not normalized_workspace_id or not normalized_message_id or not normalized_invitee_email:
            return None

        config = _resolve_durable_store_config()
        if not config:
            return None

        record, error = _read_durable_record(
            config,
            build_thread_invite_key(
                normalized_workspace_id,
                normalized_message_id,
                normalized_invitee_email,
            ),
        )
        if error or not record:
            return None

        normalized_invite = normalize_collaboration_invite_record(record)
        if not normalized_invite:
            return None

        if (
            normalized_invite["workspaceId"] != normalized_workspace_id
            or normalized_invite["messageId"] != normalized_message_id
            or normalized_invite["inviteeEmail"] != normalized_invitee_email
        ):
            return None

        return normalized_invite


    def save_invite(invite_record: dict) -> tuple[dict | None, dict | None]:
        normalized_invite = normalize_collaboration_invite_record(invite_record)
        if not normalized_invite:
            return None, {
                "code": "invalid_invite",
                "message": "Invite record is invalid.",
            }

        config = _resolve_durable_store_config()
        if not config:
            return None, {
                "code": "collaboration_store_unavailable",
                "message": "Collaboration store is not configured.",
            }

        _, error = _write_durable_record(
            config,
            build_invite_key(normalized_invite["token"]),
            normalized_invite,
        )
        if error:
            return None, error

        _, pointer_error = _write_durable_record(
            config,
            build_thread_invite_key(
                normalized_invite["workspaceId"],
                normalized_invite["messageId"],
                normalized_invite["inviteeEmail"],
            ),
            normalized_invite,
        )
        if pointer_error:
            return None, pointer_error

        return normalized_invite, None


    def issue_invite_for_thread(
        *,
        workspace_id: str,
        mailbox_id: str,
        message_id: str,
        invitee_email: str,
        participant_id: str,
        created_by_user_id: str,
        created_by_user_name: str,
        created_at: int,
        updated_at: int,
    ) -> tuple[dict | None, dict | None]:
        normalized_workspace_id = workspace_id.strip().lower()
        normalized_mailbox_id = mailbox_id.strip()
        normalized_message_id = message_id.strip()
        normalized_invitee_email = invitee_email.strip().lower()
        normalized_participant_id = participant_id.strip()
        normalized_created_by_user_id = created_by_user_id.strip()
        normalized_created_by_user_name = created_by_user_name.strip()

        if (
            not normalized_workspace_id
            or not normalized_mailbox_id
            or not normalized_message_id
            or not normalized_invitee_email
            or not normalized_participant_id
            or not normalized_created_by_user_id
            or not normalized_created_by_user_name
        ):
            return None, {
                "code": "invalid_invite",
                "message": "Invite payload is invalid.",
            }

        existing_invite = get_thread_invite(
            normalized_workspace_id,
            normalized_message_id,
            normalized_invitee_email,
        )
        if is_active_collaboration_invite_record(existing_invite):
            return existing_invite, None

        invite_record = {
            "v": COLLABORATION_THREAD_SCHEMA_VERSION,
            "token": secrets.token_urlsafe(24),
            "workspaceId": normalized_workspace_id,
            "mailboxId": normalized_mailbox_id,
            "messageId": normalized_message_id,
            "inviteeEmail": normalized_invitee_email,
            "participantId": normalized_participant_id,
            "status": "active",
            "createdAt": created_at,
            "updatedAt": updated_at,
            "createdByUserId": normalized_created_by_user_id,
            "createdByUserName": normalized_created_by_user_name,
        }

        return save_invite(invite_record)


    # --- Inactive Collaboration v2 storage -----------------------------------
    #
    # v2 uses the Redis command endpoint because multi-key creation, CAS updates,
    # invitation exchange, and revocation must be atomic.  Deployments whose KV
    # transport does not support EVAL fail closed with atomic_exchange_unavailable;
    # no multi-write fallback exists.

    V2_CLUSTER_HASH_TAG = "{cuevion-collab-v2}"
    V2_KEY_PREFIX = f"cuevion:collab:v2:{V2_CLUSTER_HASH_TAG}"
    MAX_V2_KV_RESPONSE_BYTES = 524_288
    MAX_V2_SESSION_BYTES = 16_384
    V2_THREAD_RETENTION_SECONDS = 180 * 24 * 60 * 60
    V2_OWNER_IDEMPOTENCY_RETENTION_SECONDS = V2_THREAD_RETENTION_SECONDS
    MAX_V2_OWNER_IDEMPOTENCY_RECORD_BYTES = 1_024
    V2_INDEX_HMAC_ENV = "CUEVION_COLLAB_INDEX_HMAC_KEY"
    V2_INDEX_HMAC_PREVIOUS_ENV = "CUEVION_COLLAB_INDEX_HMAC_KEY_PREVIOUS"
    _V2_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
    V2_THREAD_KEY_PREFIX = f"{V2_KEY_PREFIX}:thread:"
    V2_INVITE_KEY_PREFIX = f"{V2_KEY_PREFIX}:invite:"
    _ATOMIC_GUEST_STORE_FAILURE_EVENT = (
        "cuevion_collaboration_atomic_guest_store_failure"
    )
    _ATOMIC_GUEST_LUA_MALFORMED_EVENT = (
        "cuevion_collaboration_atomic_guest_lua_malformed"
    )
    _ATOMIC_GUEST_INVITE_INVALID_EVENT = (
        "cuevion_collaboration_atomic_guest_invite_invalid"
    )
    _ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES = 128
    _ATOMIC_GUEST_INVITE_INVALID_EVENT_MAX_BYTES = 128
    _ATOMIC_GUEST_LUA_MALFORMED_PREDICATES = frozenset(
        {
            "argv_shape",
            "key_count",
            "thread_decode",
            "thread_messages",
            "thread_valid",
            "thread_id_binding",
            "invite_decode",
            "invite_valid",
            "invite_status",
            "invite_created_at",
            "invite_ttl",
            "invite_id_binding",
            "invite_token_binding",
            "invite_owner_binding",
            "invite_workspace_binding",
            "invite_mailbox_binding",
            "invite_collaboration_binding",
        }
    )
    _ATOMIC_GUEST_INVITE_INVALID_SUBPREDICATES = frozenset(
        {
            "key_count",
            "schema_version",
            "invite_id",
            "token_hash",
            "owner_email",
            "workspace_id",
            "mailbox_id",
            "collaboration_id",
            "identity_assurance",
            "allowed_actions",
            "visibility",
            "created_by_shape",
            "created_by_owner",
            "created_by_display",
            "created_at",
            "expires_at",
            "lifetime",
            "exchange_count",
            "invited_email",
            "allowed_keys",
            "exchanged_at",
            "revoked_at",
            "revoked_by",
            "active_session_hash",
            "status_active",
            "status_exchanged",
            "status_revoked",
            "status_expired",
            "status_unknown",
        }
    )
    _ATOMIC_GUEST_STORE_FAILURE_STAGES = frozenset(
        {
            "rest_empty_body",
            "rest_json_decode",
            "rest_response_shape",
            "command_payload_shape",
            "command_error_envelope",
            "command_result_envelope",
            "eval_json_decode",
            "eval_result_shape",
            "eval_status_shape",
            "lua_malformed",
            "existing_id",
            "existing_thread_reload",
            "existing_invite_normalization",
            "existing_invite_create",
        }
    )


    @dataclass(frozen=True, slots=True)
    class _V2RecordResult:
        """Internal storage result. Never serialize this object to an HTTP client."""

        record: dict
        created: bool | None = None
        status: str = "ok"

        def get(self, name: str, default=None):
            if name == "status":
                return self.status
            if name == "record":
                return self.record
            if name == "session" and self.status == "updated":
                return self.record
            if name == "created" and self.created is not None:
                return self.created
            return default

        def __getitem__(self, name: str):
            value = self.get(name, self)
            if value is self:
                raise KeyError(name)
            return value


    @dataclass(frozen=True, slots=True)
    class _V2OwnerAppendResult:
        """Canonical committed owner-append outcome recovered from Redis."""

        message: dict
        updated_at: int
        recovered: bool
        status: str = "ok"

        def get(self, name: str, default=None):
            return {
                "status": self.status,
                "message": self.message,
                "updatedAt": self.updated_at,
                "recovered": self.recovered,
            }.get(name, default)


    @dataclass(frozen=True, slots=True)
    class _V2ThreadInviteCreateResult:
        """Atomic external-first creation or safe existing-thread convergence."""

        thread: dict
        invite: dict
        thread_created: bool
        invite_created: bool
        status: str = "ok"

        def get(self, name: str, default=None):
            return {
                "status": self.status,
                "thread": self.thread,
                "invite": self.invite,
                "threadCreated": self.thread_created,
                "inviteCreated": self.invite_created,
            }.get(name, default)


    def resolve_v2_index_hmac_key(value: str | None = None) -> bytes | None:
        encoded = os.getenv(V2_INDEX_HMAC_ENV, "") if value is None else value
        if (
            not isinstance(encoded, str)
            or not encoded
            or len(encoded) > 1024
            or not _V2_BASE64URL_RE.fullmatch(encoded)
        ):
            return None
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            return None
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        return decoded if len(decoded) >= 32 and hmac.compare_digest(canonical, encoded) else None


    def resolve_v2_index_hmac_keys(
        current_value: str | None = None,
        previous_value: str | None = None,
    ) -> tuple[bytes, bytes | None] | None:
        current = resolve_v2_index_hmac_key(current_value)
        encoded_previous = (
            os.getenv(V2_INDEX_HMAC_PREVIOUS_ENV, "")
            if previous_value is None
            else previous_value
        )
        previous = resolve_v2_index_hmac_key(encoded_previous) if encoded_previous else None
        if current is None or (encoded_previous and previous is None):
            return None
        if previous is not None and hmac.compare_digest(current, previous):
            return None
        return current, previous


    def _v2_index_digest(domain: str, parts: list, hmac_key: bytes | None = None) -> str | None:
        key = resolve_v2_index_hmac_key() if hmac_key is None else hmac_key
        if not isinstance(key, bytes) or len(key) < 32:
            return None
        payload = json.dumps(
            {"domain": f"cuevion-collaboration-v2/index-v1/{domain}", "parts": parts},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()


    def build_v2_thread_key(collaboration_id: str) -> str | None:
        if not isinstance(collaboration_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", collaboration_id):
            return None
        return f"{V2_THREAD_KEY_PREFIX}{collaboration_id}"


    def build_v2_source_thread_key(
        owner_email: str,
        mailbox_id: str,
        source_ref: dict,
        *,
        hmac_key: bytes | None = None,
    ) -> str | None:
        owner = normalize_v2_email(owner_email)
        source = normalize_v2_source_ref(source_ref)
        if owner is None or not isinstance(mailbox_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_id) or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in mailbox_id) or source is None:
            return None
        key = resolve_v2_index_hmac_key() if hmac_key is None else hmac_key
        owner_digest = _v2_index_digest("owner", [owner], key)
        source_digest = _v2_index_digest("source", [source], key)
        digest = _v2_index_digest("combined-source-index", [owner_digest, mailbox_id, source_digest], key)
        return f"{V2_KEY_PREFIX}:source-thread:{digest}" if digest else None


    def build_v2_owner_idempotency_key(
        idempotency_key: str,
        *,
        hmac_key: bytes | None = None,
    ) -> str | None:
        canonical = normalize_v2_owner_idempotency_key(idempotency_key)
        if canonical is None:
            return None
        key = resolve_v2_index_hmac_key() if hmac_key is None else hmac_key
        digest = _v2_index_digest(
            "owner-append-idempotency",
            [canonical],
            key,
        )
        return f"{V2_KEY_PREFIX}:owner-idempotency:{digest}" if digest else None


    def build_v2_invite_key(invite_id: str) -> str | None:
        if not isinstance(invite_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22,128}", invite_id):
            return None
        return f"{V2_INVITE_KEY_PREFIX}{invite_id}"


    def build_v2_invite_token_key(token_hash: str) -> str | None:
        return f"{V2_KEY_PREFIX}:invite-token:{token_hash}" if isinstance(token_hash, str) and re.fullmatch(r"[0-9a-f]{64}", token_hash) else None


    def build_v2_thread_invite_key(
        owner_email: str,
        collaboration_id: str,
        invited_email: str | None,
        hmac_key: bytes | None = None,
    ) -> str | None:
        owner = normalize_v2_email(owner_email)
        invitee = normalize_v2_email(invited_email) if invited_email is not None else None
        if owner is None or (invited_email is not None and invitee is None) or build_v2_thread_key(collaboration_id) is None:
            return None
        key = resolve_v2_index_hmac_key() if hmac_key is None else hmac_key
        owner_digest = _v2_index_digest("owner", [owner], key)
        invitee_digest = _v2_index_digest("invitee", [invitee or "no-email"], key)
        digest = _v2_index_digest("combined-invite-index", [owner_digest, collaboration_id, invitee_digest], key)
        return f"{V2_KEY_PREFIX}:thread-invite:{digest}" if digest else None


    def build_v2_guest_session_key(session_hash: str) -> str | None:
        return f"{V2_KEY_PREFIX}:guest-session:{session_hash}" if isinstance(session_hash, str) and re.fullmatch(r"[0-9a-f]{64}", session_hash) else None


    def build_v2_external_guest_index_key(collaboration_id: str) -> str | None:
        return (
            f"{V2_KEY_PREFIX}:thread-external-invites:{collaboration_id}"
            if build_v2_thread_key(collaboration_id) is not None
            else None
        )


    def _normalize_v2_external_guest_index(value: object) -> dict | None:
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "inviteIds"}
            or value.get("v") != "1"
            or not isinstance(value.get("inviteIds"), list)
            or len(value["inviteIds"]) > MAX_V2_EXTERNAL_GUESTS
        ):
            return None
        invite_ids = value["inviteIds"]
        if (
            any(build_v2_invite_key(invite_id) is None for invite_id in invite_ids)
            or len(set(invite_ids)) != len(invite_ids)
            or invite_ids != sorted(invite_ids)
        ):
            return None
        return {"v": "1", "inviteIds": list(invite_ids)}


    def _v2_error(code: str) -> dict:
        return {"status": "unavailable", "error": {"code": code}}


    def _new_atomic_guest_store_protocol_failure_observer():
        emitted = False

        def observe(stage: str) -> None:
            nonlocal emitted
            if (
                emitted
                or type(stage) is not str
                or stage not in _ATOMIC_GUEST_STORE_FAILURE_STAGES
            ):
                return
            emitted = True
            try:
                event = {
                    "event": _ATOMIC_GUEST_STORE_FAILURE_EVENT,
                    "stage": stage,
                    "internalSafeCode": "storage_protocol_error",
                }
                print(
                    json.dumps(
                        event,
                        allow_nan=False,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception:
                pass

        return observe


    def _new_atomic_guest_lua_malformed_observer():
        emitted = False

        def observe(predicate: str) -> None:
            nonlocal emitted
            if (
                emitted
                or type(predicate) is not str
                or predicate not in _ATOMIC_GUEST_LUA_MALFORMED_PREDICATES
            ):
                return
            emitted = True
            try:
                event = {
                    "event": _ATOMIC_GUEST_LUA_MALFORMED_EVENT,
                    "predicate": predicate,
                }
                serialized = json.dumps(
                    event,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if (
                    len(serialized.encode("utf-8"))
                    > _ATOMIC_GUEST_LUA_MALFORMED_EVENT_MAX_BYTES
                ):
                    return
                print(serialized, flush=True)
            except Exception:
                pass

        return observe


    def _new_atomic_guest_invite_invalid_observer():
        emitted = False

        def observe(subpredicate: str) -> None:
            nonlocal emitted
            if (
                emitted
                or type(subpredicate) is not str
                or subpredicate not in _ATOMIC_GUEST_INVITE_INVALID_SUBPREDICATES
            ):
                return
            try:
                event = {
                    "event": _ATOMIC_GUEST_INVITE_INVALID_EVENT,
                    "subpredicate": subpredicate,
                }
                serialized = json.dumps(
                    event,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if (
                    len(serialized.encode("utf-8"))
                    > _ATOMIC_GUEST_INVITE_INVALID_EVENT_MAX_BYTES
                ):
                    return
                emitted = True
                print(serialized, flush=True)
            except Exception:
                pass

        return observe


    def _notify_v2_protocol_failure(observer, stage: str) -> None:
        if observer is None:
            return
        try:
            observer(stage)
        except Exception:
            pass


    def _is_exact_v2_storage_protocol_failure(value: object) -> bool:
        if type(value) is not dict or set(value) != {"status", "error"}:
            return False
        error = value.get("error")
        return (
            value.get("status") in {"malformed", "unavailable"}
            and type(error) is dict
            and set(error) == {"code"}
            and error.get("code") == "storage_protocol_error"
        )


    def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result


    def _reject_nonstandard_json_constant(_token: str):
        raise ValueError("nonstandard JSON constant")


    def _strict_json_loads(raw: str, *, reject_numbers: bool = False):
        options = {
            "object_pairs_hook": _strict_json_object,
            "parse_constant": _reject_nonstandard_json_constant,
        }
        if reject_numbers:
            options["parse_int"] = _v2_reject_wire_number
            options["parse_float"] = _v2_reject_wire_number
        return json.loads(raw, **options)


    def _perform_v2_rest_command(
        config: dict,
        command: list,
        *,
        protocol_failure_observer=None,
    ) -> dict:
        request = Request(
            config["rest_url"],
            data=json.dumps(command, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config['rest_token']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                body = response.read(MAX_V2_KV_RESPONSE_BYTES + 1)
                if len(body) > MAX_V2_KV_RESPONSE_BYTES:
                    return _v2_error("storage_unavailable")
                if not body:
                    _notify_v2_protocol_failure(
                        protocol_failure_observer, "rest_empty_body"
                    )
                    return _v2_error("storage_protocol_error")
                try:
                    payload = _strict_json_loads(body.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    _notify_v2_protocol_failure(
                        protocol_failure_observer, "rest_json_decode"
                    )
                    return _v2_error("storage_protocol_error")
                if not isinstance(payload, dict):
                    _notify_v2_protocol_failure(
                        protocol_failure_observer, "rest_response_shape"
                    )
                    return _v2_error("storage_protocol_error")
                return payload
        except (HTTPError, URLError, TimeoutError, OSError):
            return _v2_error("storage_unavailable")


    def _v2_command(
        command: list,
        command_transport=None,
        *,
        protocol_failure_observer=None,
    ) -> dict:
        try:
            if command_transport is not None:
                payload = command_transport(command)
            else:
                config = _resolve_durable_store_config()
                if not config:
                    return _v2_error("storage_unavailable")
                payload = (
                    _perform_v2_rest_command(config, command)
                    if protocol_failure_observer is None
                    else _perform_v2_rest_command(
                        config,
                        command,
                        protocol_failure_observer=protocol_failure_observer,
                    )
                )
        except Exception:
            return _v2_error("storage_unavailable")
        if type(payload) is not dict:
            _notify_v2_protocol_failure(
                protocol_failure_observer, "command_payload_shape"
            )
            return _v2_error("storage_protocol_error")
        fields = set(payload)
        if fields == {"status", "error"}:
            error = payload.get("error")
            if (
                payload.get("status") == "unavailable"
                and type(error) is dict
                and set(error) == {"code"}
                and error.get("code") in {
                    "storage_unavailable",
                    "storage_protocol_error",
                }
            ):
                return _v2_error(error["code"])
            _notify_v2_protocol_failure(
                protocol_failure_observer, "command_error_envelope"
            )
            return _v2_error("storage_protocol_error")
        if fields == {"error"}:
            if type(payload["error"]) is str and payload["error"]:
                return _v2_error("storage_unavailable")
            _notify_v2_protocol_failure(
                protocol_failure_observer, "command_error_envelope"
            )
            return _v2_error("storage_protocol_error")
        if fields != {"result"}:
            _notify_v2_protocol_failure(
                protocol_failure_observer, "command_result_envelope"
            )
            return _v2_error("storage_protocol_error")
        return {"status": "ok", "result": payload["result"]}


    def _v2_eval(
        command: list,
        command_transport=None,
        *,
        response_shapes: dict[str, set[str]],
        optional_response_fields: dict[str, set[str]] | None = None,
        exchange: bool = False,
        protocol_failure_observer=None,
    ) -> dict:
        result = (
            _v2_command(command, command_transport)
            if protocol_failure_observer is None
            else _v2_command(
                command,
                command_transport,
                protocol_failure_observer=protocol_failure_observer,
            )
        )
        if result["status"] != "ok":
            result_code = (result.get("error") or {}).get("code")
            code = (
                "storage_protocol_error"
                if result_code == "storage_protocol_error"
                else "atomic_exchange_unavailable" if exchange else "storage_unavailable"
            )
            return {"status": "unavailable", "error": {"code": code}}
        value = result.get("result")
        if isinstance(value, str):
            try:
                value = _strict_json_loads(value)
            except (ValueError, RecursionError):
                _notify_v2_protocol_failure(
                    protocol_failure_observer, "eval_json_decode"
                )
                return {
                    "status": "unavailable",
                    "error": {"code": "storage_protocol_error"},
                }
        if type(value) is not dict or type(value.get("status")) is not str:
            _notify_v2_protocol_failure(
                protocol_failure_observer, "eval_result_shape"
            )
            return {
                "status": "unavailable",
                "error": {"code": "storage_protocol_error"},
            }
        expected_fields = response_shapes.get(value["status"])
        optional_fields = (
            optional_response_fields.get(value["status"], set())
            if optional_response_fields is not None
            else set()
        )
        actual_fields = set(value) - {"status"}
        if (
            expected_fields is None
            or not expected_fields <= actual_fields
            or not actual_fields <= expected_fields | optional_fields
        ):
            _notify_v2_protocol_failure(
                protocol_failure_observer, "eval_status_shape"
            )
            return {"status": "unavailable", "error": {"code": "storage_protocol_error"}}
        return {
            "status": value["status"],
            **{field: value[field] for field in sorted(actual_fields)},
        }


    def _v2_wire_limit(record_kind: str) -> int | None:
        return {
            "thread": MAX_V2_THREAD_BYTES,
            "invite": MAX_V2_INVITE_BYTES,
            "session": MAX_V2_SESSION_BYTES,
        }.get(record_kind)


    def _v2_wire_json(record: dict, record_kind: str) -> str | None:
        try:
            wire = encode_v2_wire_record(record, record_kind)
            if wire is None:
                return None
            encoded = json.dumps(
                wire,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            encoded_bytes = encoded.encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeEncodeError, RecursionError):
            return None
        limit = _v2_wire_limit(record_kind)
        return (
            encoded
            if limit is not None and len(encoded_bytes) <= limit
            else None
        )


    def _v2_record_from_wire(value: object, record_kind: str) -> dict | None:
        try:
            return decode_v2_wire_record(value, record_kind)
        except RecursionError:
            return None


    def _v2_reject_wire_number(_token: str):
        raise ValueError("v2 Redis wire records must not contain JSON number tokens")


    def _v2_json_from_wire(raw: object, record_kind: str) -> dict | None:
        limit = _v2_wire_limit(record_kind)
        if not isinstance(raw, str) or limit is None:
            return None
        try:
            if len(raw.encode("utf-8")) > limit:
                return None
            decoded = _strict_json_loads(raw, reject_numbers=True)
            return _v2_record_from_wire(decoded, record_kind)
        except (UnicodeEncodeError, ValueError, json.JSONDecodeError, RecursionError):
            return None


    def _v2_read_json(key: str, record_kind: str, command_transport=None) -> dict:
        result = _v2_command(["GET", key], command_transport)
        if result["status"] != "ok":
            return result
        value = result.get("result")
        if value is None:
            return {"status": "missing"}
        value = _v2_json_from_wire(value, record_kind)
        if value is None:
            return {"status": "malformed"}
        return {"status": "ok", "record": value}


    _V2_LUA_COMMON = r"""
    local MAX_SAFE = 9007199254740991
    local MIN_SECONDS = 1577836800
    local MAX_SECONDS = 4102444800
    local MIN_MILLISECONDS = 1577836800000
    local MAX_MILLISECONDS = 4102444800999
    local JSON_NULL = cjson.null
    local function keyCount(value)
      local count = 0
      for _, _ in pairs(value) do count = count + 1 end
      return count
    end
    local function rawIsValidUtf8(raw)
      if type(raw) ~= 'string' then return false end
      local cursor = 1
      while cursor <= #raw do
        local first = string.byte(raw, cursor)
        if first <= 127 then
          cursor = cursor + 1
        else
          local second = string.byte(raw, cursor + 1)
          local third = string.byte(raw, cursor + 2)
          local fourth = string.byte(raw, cursor + 3)
          if first >= 194 and first <= 223 then
            if not second or second < 128 or second > 191 then return false end
            cursor = cursor + 2
          elseif first == 224 then
            if not second or second < 160 or second > 191
              or not third or third < 128 or third > 191 then return false end
            cursor = cursor + 3
          elseif (first >= 225 and first <= 236) or (first >= 238 and first <= 239) then
            if not second or second < 128 or second > 191
              or not third or third < 128 or third > 191 then return false end
            cursor = cursor + 3
          elseif first == 237 then
            if not second or second < 128 or second > 159
              or not third or third < 128 or third > 191 then return false end
            cursor = cursor + 3
          elseif first == 240 then
            if not second or second < 144 or second > 191
              or not third or third < 128 or third > 191
              or not fourth or fourth < 128 or fourth > 191 then return false end
            cursor = cursor + 4
          elseif first >= 241 and first <= 243 then
            if not second or second < 128 or second > 191
              or not third or third < 128 or third > 191
              or not fourth or fourth < 128 or fourth > 191 then return false end
            cursor = cursor + 4
          elseif first == 244 then
            if not second or second < 128 or second > 143
              or not third or third < 128 or third > 191
              or not fourth or fourth < 128 or fourth > 191 then return false end
            cursor = cursor + 4
          else
            return false
          end
        end
      end
      return true
    end
    local function rawHasNoJsonNumbers(raw)
      if type(raw) ~= 'string' then return false end
      local inString = false
      local escaped = false
      for index = 1, #raw do
        local byte = string.byte(raw, index)
        if inString then
          if escaped then
            escaped = false
          elseif byte == 92 then
            escaped = true
          elseif byte == 34 then
            inString = false
          end
        elseif byte == 34 then
          inString = true
        elseif byte == 45 or (byte >= 48 and byte <= 57) then
          return false
        end
      end
      return not inString and not escaped
    end
    local function jsonWhitespace(byte)
      return byte == 32 or byte == 9 or byte == 10 or byte == 13
    end
    local function skipJsonWhitespace(raw, cursor)
      while cursor <= #raw and jsonWhitespace(string.byte(raw, cursor)) do cursor = cursor + 1 end
      return cursor
    end
    local function jsonHexCode(raw, cursor)
      local value = 0
      for offset = 0, 3 do
        local byte = string.byte(raw, cursor + offset)
        local digit = nil
        if byte and byte >= 48 and byte <= 57 then digit = byte - 48
        elseif byte and byte >= 65 and byte <= 70 then digit = byte - 55
        elseif byte and byte >= 97 and byte <= 102 then digit = byte - 87
        else return nil end
        value = value * 16 + digit
      end
      return value
    end
    local function jsonUtf8(codepoint)
      if codepoint <= 127 then return string.char(codepoint) end
      if codepoint <= 2047 then
        return string.char(192 + math.floor(codepoint / 64), 128 + (codepoint % 64))
      end
      if codepoint <= 65535 then
        if codepoint >= 55296 and codepoint <= 57343 then return nil end
        return string.char(
          224 + math.floor(codepoint / 4096),
          128 + (math.floor(codepoint / 64) % 64),
          128 + (codepoint % 64)
        )
      end
      if codepoint <= 1114111 then
        return string.char(
          240 + math.floor(codepoint / 262144),
          128 + (math.floor(codepoint / 4096) % 64),
          128 + (math.floor(codepoint / 64) % 64),
          128 + (codepoint % 64)
        )
      end
      return nil
    end
    local function parseJsonString(raw, cursor)
      if string.byte(raw, cursor) ~= 34 then return nil, nil end
      cursor = cursor + 1
      local pieces = {}
      while cursor <= #raw do
        local byte = string.byte(raw, cursor)
        if byte == 34 then return cursor + 1, table.concat(pieces) end
        if byte == 92 then
          local escaped = string.byte(raw, cursor + 1)
          local simple = {
            [34] = string.char(34), [47] = string.char(47), [92] = string.char(92),
            [98] = string.char(8), [102] = string.char(12), [110] = string.char(10),
            [114] = string.char(13), [116] = string.char(9)
          }
          if simple[escaped] then
            table.insert(pieces, simple[escaped])
            cursor = cursor + 2
          elseif escaped == 117 then
            local codepoint = jsonHexCode(raw, cursor + 2)
            if not codepoint then return nil, nil end
            cursor = cursor + 6
            if codepoint >= 55296 and codepoint <= 56319 then
              if string.byte(raw, cursor) ~= 92 or string.byte(raw, cursor + 1) ~= 117 then return nil, nil end
              local low = jsonHexCode(raw, cursor + 2)
              if not low or low < 56320 or low > 57343 then return nil, nil end
              codepoint = 65536 + (codepoint - 55296) * 1024 + (low - 56320)
              cursor = cursor + 6
            elseif codepoint >= 56320 and codepoint <= 57343 then
              return nil, nil
            end
            local encoded = jsonUtf8(codepoint)
            if not encoded then return nil, nil end
            table.insert(pieces, encoded)
          else
            return nil, nil
          end
        else
          if byte < 32 then return nil, nil end
          table.insert(pieces, string.char(byte))
          cursor = cursor + 1
        end
      end
      return nil, nil
    end
    local parseJsonValue
    local function parseJsonObject(raw, cursor, depth)
      cursor = skipJsonWhitespace(raw, cursor + 1)
      if string.byte(raw, cursor) == 125 then return cursor + 1 end
      local seen = {}
      while cursor <= #raw do
        local nextCursor, key = parseJsonString(raw, cursor)
        if not nextCursor or seen[key] then return nil end
        seen[key] = true
        cursor = skipJsonWhitespace(raw, nextCursor)
        if string.byte(raw, cursor) ~= 58 then return nil end
        cursor = parseJsonValue(raw, cursor + 1, depth + 1)
        if not cursor then return nil end
        cursor = skipJsonWhitespace(raw, cursor)
        local byte = string.byte(raw, cursor)
        if byte == 125 then return cursor + 1 end
        if byte ~= 44 then return nil end
        cursor = skipJsonWhitespace(raw, cursor + 1)
      end
      return nil
    end
    local function parseJsonArray(raw, cursor, depth)
      cursor = skipJsonWhitespace(raw, cursor + 1)
      if string.byte(raw, cursor) == 93 then return cursor + 1 end
      while cursor <= #raw do
        cursor = parseJsonValue(raw, cursor, depth + 1)
        if not cursor then return nil end
        cursor = skipJsonWhitespace(raw, cursor)
        local byte = string.byte(raw, cursor)
        if byte == 93 then return cursor + 1 end
        if byte ~= 44 then return nil end
        cursor = skipJsonWhitespace(raw, cursor + 1)
      end
      return nil
    end
    parseJsonValue = function(raw, cursor, depth)
      if depth > 64 then return nil end
      cursor = skipJsonWhitespace(raw, cursor)
      local byte = string.byte(raw, cursor)
      if byte == 123 then return parseJsonObject(raw, cursor, depth) end
      if byte == 91 then return parseJsonArray(raw, cursor, depth) end
      if byte == 34 then
        local nextCursor, _ = parseJsonString(raw, cursor)
        return nextCursor
      end
      if string.sub(raw, cursor, cursor + 3) == 'true' then return cursor + 4 end
      if string.sub(raw, cursor, cursor + 4) == 'false' then return cursor + 5 end
      if string.sub(raw, cursor, cursor + 3) == 'null' then return cursor + 4 end
      return nil
    end
    local function rawTopLevelArray(raw, field)
      if type(raw) ~= 'string' or type(field) ~= 'string' then return false end
      local cursor = skipJsonWhitespace(raw, 1)
      if string.byte(raw, cursor) ~= 123 then return false end
      cursor = skipJsonWhitespace(raw, cursor + 1)
      if string.byte(raw, cursor) == 125 then return false end
      local seen = {}
      local found = false
      while cursor <= #raw do
        local nextCursor, key = parseJsonString(raw, cursor)
        if not nextCursor or seen[key] then return false end
        seen[key] = true
        cursor = skipJsonWhitespace(raw, nextCursor)
        if string.byte(raw, cursor) ~= 58 then return false end
        local valueCursor = skipJsonWhitespace(raw, cursor + 1)
        if key == field then found = string.byte(raw, valueCursor) == 91 end
        cursor = parseJsonValue(raw, valueCursor, 1)
        if not cursor then return false end
        cursor = skipJsonWhitespace(raw, cursor)
        local byte = string.byte(raw, cursor)
        if byte == 125 then
          cursor = skipJsonWhitespace(raw, cursor + 1)
          return cursor == #raw + 1 and found
        end
        if byte ~= 44 then return false end
        cursor = skipJsonWhitespace(raw, cursor + 1)
      end
      return false
    end
    local function rawHasUniqueObjectKeys(raw)
      if type(raw) ~= 'string' then return false end
      local cursor = parseJsonValue(raw, 1, 0)
      return cursor ~= nil and skipJsonWhitespace(raw, cursor) == #raw + 1
    end
    local function decodeWire(raw)
      if not rawIsValidUtf8(raw) or not rawHasNoJsonNumbers(raw)
        or not rawHasUniqueObjectKeys(raw) then return false, nil end
      return pcall(cjson.decode, raw)
    end
    local function integerValue(value)
      if type(value) ~= 'string' or #value == 0 or #value > 16 then return nil end
      if value ~= '0' and string.match(value, '^[1-9][0-9]*$') == nil then return nil end
      local parsed = tonumber(value)
      if not parsed or parsed ~= math.floor(parsed) or parsed < 0 or parsed > MAX_SAFE then return nil end
      return parsed
    end
    local function exactInteger(value)
      return integerValue(value) ~= nil
    end
    local function timestampSeconds(value)
      local parsed = integerValue(value)
      return parsed ~= nil and parsed >= MIN_SECONDS and parsed <= MAX_SECONDS
    end
    local function timestampMilliseconds(value)
      local parsed = integerValue(value)
      return parsed ~= nil and parsed >= MIN_MILLISECONDS and parsed <= MAX_MILLISECONDS
    end
    local function positiveInteger(value)
      local parsed = integerValue(value)
      return parsed ~= nil and parsed > 0
    end
    local function redisStringType(key)
      local kind = redis.call('TYPE', key)
      if type(kind) == 'table' then kind = kind.ok end
      return kind
    end
    local function readString(key, maximum)
      local kind = redisStringType(key)
      if kind == 'none' then return 'missing', nil end
      if kind ~= 'string' then return 'invalid', nil end
      local raw = redis.call('GET', key)
      if type(raw) ~= 'string' or #raw > maximum then return 'invalid', nil end
      return 'ok', raw
    end
    local function asciiSecurityString(value, maximum, allowEmpty)
      if type(value) ~= 'string' or #value > maximum or (not allowEmpty and #value == 0) then return false end
      if #value > 0 and (string.byte(value, 1) == 32 or string.byte(value, #value) == 32) then return false end
      for index = 1, #value do
        local byte = string.byte(value, index)
        if byte < 32 or byte == 127 or byte > 126 then return false end
      end
      return true
    end
    local function hiddenUnicode(value)
      for index = 1, #value do
        local a = string.byte(value, index)
        local b = string.byte(value, index + 1) or -1
        local c = string.byte(value, index + 2) or -1
        local d = string.byte(value, index + 3) or -1
        if (a == 194 and ((b >= 128 and b <= 159) or b == 173))
          or (a == 216 and ((b >= 128 and b <= 133) or b == 156))
          or (a == 219 and b == 157) or (a == 220 and b == 143)
          or (a == 224 and b == 162 and (c == 144 or c == 145))
          or (a == 224 and b == 163 and c == 162)
          or (a == 225 and b == 160 and c == 142)
          or (a == 226 and b == 128 and ((c >= 139 and c <= 143) or (c >= 170 and c <= 174)))
          or (a == 226 and b == 129 and ((c >= 160 and c <= 164) or (c >= 166 and c <= 175)))
          or (a == 237 and b >= 160 and b <= 191)
          or (a == 239 and b == 187 and c == 191)
          or (a == 239 and b == 191 and c >= 185 and c <= 187)
          or (a == 240 and b == 145 and ((c == 130 and d == 189) or (c == 131 and d == 141)))
          or (a == 240 and b == 147 and c == 144 and d >= 176 and d <= 184)
          or (a == 240 and b == 155 and c == 178 and d >= 160 and d <= 163)
          or (a == 240 and b == 157 and c == 133 and d >= 179 and d <= 186)
          or (a == 243 and b == 160 and ((c == 128 and (d == 129 or d >= 160)) or c == 129)) then
          return true
        end
      end
      return false
    end
    local function unicodeEdgeWhitespace(value)
      local fixed = {
        string.char(194, 133), string.char(194, 160), string.char(225, 154, 128),
        string.char(226, 128, 168), string.char(226, 128, 169),
        string.char(226, 128, 175), string.char(226, 129, 159),
        string.char(227, 128, 128)
      }
      for _, marker in ipairs(fixed) do
        if string.sub(value, 1, #marker) == marker or string.sub(value, -#marker) == marker then
          return true
        end
      end
      for finalByte = 128, 138 do
        local marker = string.char(226, 128, finalByte)
        if string.sub(value, 1, 3) == marker or string.sub(value, -3) == marker then return true end
      end
      return false
    end
    local function displayString(value, maximum, allowEmpty)
      if type(value) ~= 'string' or #value > maximum or (not allowEmpty and #value == 0) then return false end
      if #value > 0 and (
        string.byte(value, 1) == 32 or string.byte(value, #value) == 32
        or unicodeEdgeWhitespace(value)
      ) then return false end
      return string.find(value, '[%z\1-\31\127]') == nil and not hiddenUnicode(value)
    end
    local function freeText(value, maximum)
      if type(value) ~= 'string' or #value > maximum then return false end
      for index = 1, #value do
        local byte = string.byte(value, index)
        if (byte < 32 and byte ~= 9 and byte ~= 10 and byte ~= 13) or byte == 127 then return false end
      end
      return not hiddenUnicode(value)
    end
    local function opaqueId(value)
      return asciiSecurityString(value, 128, false) and #value >= 22
        and string.match(value, '^[A-Za-z0-9_-]+$') ~= nil
    end
    local function mailboxId(value)
      return asciiSecurityString(value, 256, false) and value == string.lower(value)
        and string.match(value, '^[a-z0-9][a-z0-9._:-]*$') ~= nil
    end
    local function canonicalWorkspaceId(value)
      return asciiSecurityString(value, 26, false) and #value == 26
        and string.match(value, '^wsp_[A-Za-z0-9_-]+$') ~= nil
    end
    local function canonicalUserId(value)
      if not asciiSecurityString(value, 26, false) or #value ~= 26
        or string.sub(value, 1, 4) ~= 'usr_'
        or string.match(string.sub(value, 5), '^[A-Za-z0-9_-]+$') == nil then return false end
      local final = string.sub(value, -1)
      return final == 'A' or final == 'Q' or final == 'g' or final == 'w'
    end
    local function membershipRef(value)
      return asciiSecurityString(value, 69, false) and #value >= 6
        and string.sub(value, 1, 5) == 'tinv_'
        and string.match(string.sub(value, 6), '^[A-Za-z0-9_-]+$') ~= nil
    end
    local function canonicalEmail(value)
      if not asciiSecurityString(value, 320, false) or value ~= string.lower(value) then return false end
      local at = string.find(value, '@', 1, true)
      if not at or string.find(value, '@', at + 1, true) then return false end
      local localPart = string.sub(value, 1, at - 1)
      local domain = string.sub(value, at + 1)
      if #localPart < 1 or #localPart > 64 or #domain < 3 or #domain > 253
        or string.sub(localPart, 1, 1) == '.' or string.sub(localPart, -1) == '.'
        or string.find(localPart, '..', 1, true)
        or string.match(localPart, '^[A-Za-z0-9!#$%%&\'_*+/=?^`{|}~.-]+$') == nil
        or string.sub(domain, 1, 1) == '.' or string.sub(domain, -1) == '.'
        or string.find(domain, '..', 1, true) or not string.find(domain, '.', 1, true) then return false end
      for label in string.gmatch(domain, '[^.]+') do
        if #label < 1 or #label > 63 or string.match(label, '^[a-z0-9-]+$') == nil
          or string.sub(label, 1, 1) == '-' or string.sub(label, -1) == '-' then return false end
      end
      return true
    end
    local function denseStringArray(value, first, second)
      if type(value) ~= 'table' or #value ~= 2 or keyCount(value) ~= 2 then return false end
      return value[1] == first and value[2] == second
    end
    local function sourceValid(value)
      if type(value) ~= 'table' then return false end
      if value.provider == 'google' then
        return keyCount(value) == 2 and asciiSecurityString(value.providerMessageId, 512, false)
      end
      if value.provider == 'custom_imap' then
        return keyCount(value) == 4 and value.folder == 'INBOX'
          and type(value.uidValidity) == 'string' and string.match(value.uidValidity, '^[1-9][0-9]*$') ~= nil and #value.uidValidity <= 20
          and type(value.imapUid) == 'string' and string.match(value.imapUid, '^[1-9][0-9]*$') ~= nil and #value.imapUid <= 20
      end
      return false
    end
    local function sourceEqual(a, b)
      if type(a) ~= 'table' or type(b) ~= 'table' or a.provider ~= b.provider then return false end
      if a.provider == 'google' then return a.providerMessageId == b.providerMessageId end
      return a.folder == b.folder and a.uidValidity == b.uidValidity and a.imapUid == b.imapUid
    end
    local function sourceMessageValid(value)
      return type(value) == 'table' and keyCount(value) == 5
        and displayString(value.subject, 998, true) and displayString(value.senderDisplay, 512, true)
        and displayString(value.fromDisplay, 512, true) and displayString(value.timestamp, 128, true)
        and freeText(value.bodyText, 131072)
    end
    local function sourceMessageEqual(a, b)
      return a.subject == b.subject and a.senderDisplay == b.senderDisplay
        and a.fromDisplay == b.fromDisplay and a.timestamp == b.timestamp and a.bodyText == b.bodyText
    end
    local function messageValid(message)
      return type(message) == 'table' and keyCount(message) == 6 and opaqueId(message.id)
        and (message.authorKind == 'owner' or message.authorKind == 'internal' or message.authorKind == 'guest' or message.authorKind == 'system')
        and displayString(message.authorDisplayName, 256, false) and freeText(message.text, 16384)
        and (message.visibility == 'internal' or message.visibility == 'shared')
        and timestampMilliseconds(message.createdAt)
    end
    local function messageEqual(a, b)
      return a.id == b.id and a.authorKind == b.authorKind and a.authorDisplayName == b.authorDisplayName
        and a.text == b.text and a.visibility == b.visibility and a.createdAt == b.createdAt
    end
    local function messagesValid(messages)
      if type(messages) ~= 'table' or #messages > 500 then return false end
      local count = 0
      for key, message in pairs(messages) do
        if type(key) ~= 'number' or key ~= math.floor(key) or key < 1 or key > #messages
          or not messageValid(message) then return false end
        count = count + 1
      end
      return count == #messages
    end
    local function messagesEqual(a, b)
      if type(a) ~= 'table' or type(b) ~= 'table' or #a ~= #b then return false end
      for index = 1, #a do if not messageEqual(a[index], b[index]) then return false end end
      return true
    end
    local function participantValid(value)
      return type(value) == 'table' and keyCount(value) == 3
        and canonicalUserId(value.userId) and membershipRef(value.membershipRef)
        and displayString(value.displayName, 256, false)
    end
    local function participantEqual(a, b)
      return type(a) == 'table' and type(b) == 'table'
        and a.userId == b.userId and a.membershipRef == b.membershipRef
        and a.displayName == b.displayName
    end
    local function participantsValid(values, ownerUserId)
      if type(values) ~= 'table' or #values < 1 or #values > 15
        or keyCount(values) ~= #values then return false end
      local previous = nil
      for index = 1, #values do
        local value = values[index]
        if not participantValid(value) or value.userId == ownerUserId
          or (previous and previous >= value.userId) then return false end
        previous = value.userId
      end
      return true
    end
    local function participantAuthorityValid(value)
      local count = type(value) == 'table' and keyCount(value) or 0
      if count == 11 then
        return value.ownerUserId == nil and value.ownerDisplayName == nil
          and value.participants == nil
      end
      return count == 14 and canonicalWorkspaceId(value.workspaceId)
        and canonicalUserId(value.ownerUserId)
        and displayString(value.ownerDisplayName, 256, false)
        and participantsValid(value.participants, value.ownerUserId)
    end
    local function participantAuthorityEqual(a, b)
      if a.ownerUserId == nil or b.ownerUserId == nil then
        return a.ownerUserId == nil and b.ownerUserId == nil
          and a.ownerDisplayName == nil and b.ownerDisplayName == nil
          and a.participants == nil and b.participants == nil
      end
      if a.ownerUserId ~= b.ownerUserId or a.ownerDisplayName ~= b.ownerDisplayName
        or #a.participants ~= #b.participants then return false end
      for index = 1, #a.participants do
        if not participantEqual(a.participants[index], b.participants[index]) then return false end
      end
      return true
    end
    local function threadValid(value)
      local createdAt = type(value) == 'table' and integerValue(value.createdAt) or nil
      local updatedAt = type(value) == 'table' and integerValue(value.updatedAt) or nil
      return type(value) == 'table' and participantAuthorityValid(value)
        and value.v == '2' and exactInteger(value.v)
        and opaqueId(value.collaborationId) and canonicalEmail(value.ownerEmail)
        and (canonicalWorkspaceId(value.workspaceId) or value.workspaceId == value.ownerEmail)
        and mailboxId(value.mailboxId)
        and sourceValid(value.sourceRef) and sourceMessageValid(value.sourceMessage)
        and messagesValid(value.messages)
        and (value.state == 'needs_review' or value.state == 'needs_action' or value.state == 'note_only' or value.state == 'resolved')
        and timestampMilliseconds(value.createdAt) and timestampMilliseconds(value.updatedAt)
        and updatedAt >= createdAt
    end
    local function inviteValid(invite)
      if type(invite) ~= 'table' then return false, 'key_count' end
      local count = keyCount(invite)
      local createdAt = integerValue(invite.createdAt)
      local expiresAt = integerValue(invite.expiresAt)
      if count < 18 or count > 20 then return false, 'key_count' end
      if invite.v ~= '2' or not exactInteger(invite.v) then return false, 'schema_version' end
      if not opaqueId(invite.inviteId) then return false, 'invite_id' end
      if type(invite.tokenHash) ~= 'string' or #invite.tokenHash ~= 64
        or string.match(invite.tokenHash, '^[0-9a-f]+$') == nil then return false, 'token_hash' end
      if not canonicalEmail(invite.ownerEmail) then return false, 'owner_email' end
      if not canonicalWorkspaceId(invite.workspaceId) then return false, 'workspace_id' end
      if not mailboxId(invite.mailboxId) then return false, 'mailbox_id' end
      if not opaqueId(invite.collaborationId) then return false, 'collaboration_id' end
      if invite.identityAssurance ~= 'link_possession' then return false, 'identity_assurance' end
      if not denseStringArray(invite.allowedActions, 'read', 'reply') then return false, 'allowed_actions' end
      if invite.visibility ~= 'shared_only' then return false, 'visibility' end
      if type(invite.createdBy) ~= 'table' or keyCount(invite.createdBy) ~= 2 then
        return false, 'created_by_shape'
      end
      if invite.createdBy.ownerEmail ~= invite.ownerEmail then return false, 'created_by_owner' end
      if not displayString(invite.createdBy.displayName, 256, false) then return false, 'created_by_display' end
      if not timestampSeconds(invite.createdAt) then return false, 'created_at' end
      if not timestampSeconds(invite.expiresAt) then return false, 'expires_at' end
      if createdAt >= expiresAt or expiresAt - createdAt > 86400 then return false, 'lifetime' end
      if not exactInteger(invite.exchangeCount) then return false, 'exchange_count' end
      if invite.invitedEmail ~= nil and not canonicalEmail(invite.invitedEmail) then
        return false, 'invited_email'
      end
      local allowed = {v=true,inviteId=true,tokenHash=true,ownerEmail=true,workspaceId=true,mailboxId=true,
        collaborationId=true,invitedEmail=true,identityAssurance=true,allowedActions=true,visibility=true,
        createdBy=true,createdAt=true,expiresAt=true,status=true,exchangedAt=true,exchangeCount=true,
        revokedAt=true,revokedBy=true,activeSessionHash=true}
      for key, _ in pairs(invite) do if not allowed[key] then return false, 'allowed_keys' end end
      if invite.exchangedAt ~= JSON_NULL and not timestampSeconds(invite.exchangedAt) then
        return false, 'exchanged_at'
      end
      if invite.revokedAt ~= JSON_NULL and not timestampSeconds(invite.revokedAt) then
        return false, 'revoked_at'
      end
      if invite.revokedBy ~= JSON_NULL and (
        not canonicalEmail(invite.revokedBy) or invite.revokedBy ~= invite.ownerEmail
      ) then return false, 'revoked_by' end
      if invite.activeSessionHash ~= nil and (type(invite.activeSessionHash) ~= 'string'
        or #invite.activeSessionHash ~= 64
        or string.match(invite.activeSessionHash, '^[0-9a-f]+$') == nil) then
        return false, 'active_session_hash'
      end
      if invite.status == 'active' then
        if invite.exchangeCount == '0' and invite.exchangedAt == JSON_NULL and invite.revokedAt == JSON_NULL
          and invite.revokedBy == JSON_NULL and invite.activeSessionHash == nil then return true, nil end
        return false, 'status_active'
      elseif invite.status == 'exchanged' then
        local exchangedAt = integerValue(invite.exchangedAt)
        if invite.exchangeCount == '1' and timestampSeconds(invite.exchangedAt)
          and exchangedAt >= createdAt and exchangedAt < expiresAt
          and invite.revokedAt == JSON_NULL and invite.revokedBy == JSON_NULL
          and invite.activeSessionHash ~= nil then return true, nil end
        return false, 'status_exchanged'
      elseif invite.status == 'revoked' then
        local revokedAt = integerValue(invite.revokedAt)
        if not timestampSeconds(invite.revokedAt) or revokedAt <= createdAt or revokedAt >= expiresAt
          or invite.revokedBy ~= invite.ownerEmail then return false, 'status_revoked' end
        if invite.exchangeCount == '0' then
          if invite.exchangedAt == JSON_NULL and invite.activeSessionHash == nil then return true, nil end
          return false, 'status_revoked'
        end
        local exchangedAt = integerValue(invite.exchangedAt)
        if invite.exchangeCount == '1' and timestampSeconds(invite.exchangedAt)
          and exchangedAt >= createdAt and exchangedAt < revokedAt
          and exchangedAt < expiresAt and invite.activeSessionHash ~= nil then return true, nil end
        return false, 'status_revoked'
      elseif invite.status == 'expired' then
        if invite.exchangeCount == '0' and invite.exchangedAt == JSON_NULL and invite.revokedAt == JSON_NULL
          and invite.revokedBy == JSON_NULL and invite.activeSessionHash == nil then return true, nil end
        return false, 'status_expired'
      end
      return false, 'status_unknown'
    end
    local function sessionValid(session)
      if type(session) ~= 'table' then return false end
      local createdAt = integerValue(session.createdAt)
      local lastUsedAt = integerValue(session.lastUsedAt)
      local expiresAt = integerValue(session.expiresAt)
      if keyCount(session) ~= 18 or session.v ~= '2' or not exactInteger(session.v)
        or type(session.sessionHash) ~= 'string' or #session.sessionHash ~= 64 or string.match(session.sessionHash, '^[0-9a-f]+$') == nil
        or type(session.csrfTokenHash) ~= 'string' or #session.csrfTokenHash ~= 64 or string.match(session.csrfTokenHash, '^[0-9a-f]+$') == nil
        or not opaqueId(session.inviteId) or not canonicalEmail(session.ownerEmail) or not canonicalWorkspaceId(session.workspaceId)
        or not mailboxId(session.mailboxId) or not opaqueId(session.collaborationId)
        or not denseStringArray(session.allowedActions, 'read', 'reply') or session.visibility ~= 'shared_only'
        or session.identityAssurance ~= 'link_possession' or not displayString(session.guestDisplayName, 256, false)
        or not timestampSeconds(session.createdAt) or not timestampSeconds(session.lastUsedAt) or not timestampSeconds(session.expiresAt)
        or createdAt > lastUsedAt or lastUsedAt >= expiresAt
        or expiresAt - createdAt > 28800 then return false end
      if session.status == 'active' or session.status == 'expired' then
        return session.revokedAt == JSON_NULL and session.loggedOutAt == JSON_NULL
      elseif session.status == 'revoked' then
        local revokedAt = integerValue(session.revokedAt)
        return timestampSeconds(session.revokedAt) and revokedAt > lastUsedAt
          and revokedAt < expiresAt and session.loggedOutAt == JSON_NULL
      elseif session.status == 'logged_out' then
        local loggedOutAt = integerValue(session.loggedOutAt)
        return timestampSeconds(session.loggedOutAt) and loggedOutAt > lastUsedAt
          and loggedOutAt < expiresAt and session.revokedAt == JSON_NULL
      end
      return false
    end
    local function nullableJsonEqual(a, b)
      if a == JSON_NULL then a = nil end
      if b == JSON_NULL then b = nil end
      return a == b
    end
    local function sessionEqual(a, b)
      return type(a) == 'table' and type(b) == 'table'
        and a.v == b.v and a.sessionHash == b.sessionHash
        and a.csrfTokenHash == b.csrfTokenHash and a.inviteId == b.inviteId
        and a.ownerEmail == b.ownerEmail and a.workspaceId == b.workspaceId
        and a.mailboxId == b.mailboxId and a.collaborationId == b.collaborationId
        and a.allowedActions[1] == b.allowedActions[1]
        and a.allowedActions[2] == b.allowedActions[2]
        and a.visibility == b.visibility and a.identityAssurance == b.identityAssurance
        and a.guestDisplayName == b.guestDisplayName and a.createdAt == b.createdAt
        and a.lastUsedAt == b.lastUsedAt and a.expiresAt == b.expiresAt
        and a.status == b.status and nullableJsonEqual(a.revokedAt, b.revokedAt)
        and nullableJsonEqual(a.loggedOutAt, b.loggedOutAt)
    end
    """


    _VALIDATE_V2_WIRE_RECORD_LUA = _V2_LUA_COMMON + r"""
    local kind = ARGV[1]
    local raw = ARGV[2]
    local limit = nil
    if kind == 'thread' then limit = 262144
    elseif kind == 'invite' or kind == 'session' then limit = 16384
    else return cjson.encode({status='malformed'}) end
    if type(raw) ~= 'string' or #raw > limit then return cjson.encode({status='malformed'}) end
    local ok, value = decodeWire(raw)
    if not ok then return cjson.encode({status='malformed'}) end
    local valid = false
    if kind == 'thread' then valid = rawTopLevelArray(raw, 'messages') and threadValid(value)
    elseif kind == 'invite' then valid = inviteValid(value)
    else valid = sessionValid(value) end
    return cjson.encode({status=valid and 'valid' or 'malformed'})
    """.strip()


    _CREATE_V2_THREAD_LUA = _V2_LUA_COMMON + r"""
    if #ARGV[1] > 262144 or not positiveInteger(ARGV[3]) then return cjson.encode({status='malformed'}) end
    local proposedOk, proposed = decodeWire(ARGV[1])
    if not proposedOk or not rawTopLevelArray(ARGV[1], 'messages') or not threadValid(proposed) or proposed.collaborationId ~= ARGV[2] then
      return cjson.encode({status='malformed'})
    end
    local currentPointer = redis.call('GET', KEYS[2])
    local previousPointer = nil
    if #KEYS == 3 then previousPointer = redis.call('GET', KEYS[3]) end
    if currentPointer and previousPointer and currentPointer ~= previousPointer then
      return cjson.encode({status='source_pointer_conflict'})
    end
    local pointer = currentPointer or previousPointer
    if pointer then
      if not opaqueId(pointer) then return cjson.encode({status='source_pointer_conflict'}) end
      local targetKey = ARGV[4] .. pointer
      local targetRaw = redis.call('GET', targetKey)
      if targetRaw then
        if (currentPointer and redis.call('PTTL', KEYS[2]) <= 0)
          or (previousPointer and redis.call('PTTL', KEYS[3]) <= 0) then
          return cjson.encode({status='source_pointer_conflict'})
        end
        if redis.call('PTTL', targetKey) <= 0 then return cjson.encode({status='source_pointer_conflict'}) end
        if #targetRaw > 262144 or not rawTopLevelArray(targetRaw, 'messages') then return cjson.encode({status='source_pointer_conflict'}) end
        local targetOk, target = decodeWire(targetRaw)
        if not targetOk or not threadValid(target) or target.collaborationId ~= pointer
          or target.ownerEmail ~= proposed.ownerEmail or target.workspaceId ~= proposed.workspaceId
          or target.mailboxId ~= proposed.mailboxId or not sourceEqual(target.sourceRef, proposed.sourceRef)
          or not participantAuthorityEqual(target, proposed) then
          return cjson.encode({status='source_pointer_conflict'})
        end
        redis.call('EXPIRE', targetKey, ARGV[3])
        redis.call('SET', KEYS[2], pointer, 'EX', ARGV[3])
        if #KEYS == 3 then redis.call('DEL', KEYS[3]) end
        return cjson.encode({status='duplicate', collaborationId=pointer})
      end
      -- A stale source pointer is repairable only if creation can commit.  A
      -- conflicting proposed key must leave the entire namespace untouched.
      if redis.call('EXISTS', KEYS[1]) == 1 then return cjson.encode({status='conflict'}) end
      redis.call('DEL', KEYS[2])
      if #KEYS == 3 then redis.call('DEL', KEYS[3]) end
    end
    if redis.call('EXISTS', KEYS[1]) == 1 then return cjson.encode({status='conflict'}) end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
    if #KEYS == 3 then redis.call('DEL', KEYS[3]) end
    return cjson.encode({status='created'})
    """.strip()


    def _create_v2_thread(thread_record: dict, *, command_transport=None) -> dict:
        thread = normalize_v2_thread_record(thread_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        if thread is None or thread_wire is None:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        thread_key = build_v2_thread_key(thread["collaborationId"])
        hmac_keys = resolve_v2_index_hmac_keys()
        if hmac_keys is None:
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        current_hmac, previous_hmac = hmac_keys
        source_key = build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"], hmac_key=current_hmac
        )
        previous_source_key = (
            build_v2_source_thread_key(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"], hmac_key=previous_hmac
            )
            if previous_hmac is not None
            else None
        )
        if thread_key is None or source_key is None or (previous_hmac is not None and previous_source_key is None):
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        keys = [thread_key, source_key]
        if previous_source_key is not None and previous_source_key != source_key:
            keys.append(previous_source_key)
        result = _v2_eval(
            [
                "EVAL", _CREATE_V2_THREAD_LUA, len(keys), *keys,
                thread_wire,
                thread["collaborationId"],
                str(V2_THREAD_RETENTION_SECONDS),
                V2_THREAD_KEY_PREFIX,
            ],
            command_transport,
            response_shapes={
                "created": set(), "duplicate": {"collaborationId"}, "conflict": set(),
                "source_pointer_conflict": set(), "malformed": set(),
            },
        )
        if result.get("status") == "created":
            return _V2RecordResult(thread, created=True)
        if result.get("status") == "duplicate":
            existing_id = result.get("collaborationId")
            if build_v2_thread_key(existing_id) is None:
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            loaded = _load_v2_thread_by_source(
                thread["ownerEmail"],
                thread["mailboxId"],
                thread["sourceRef"],
                workspace_id=thread["workspaceId"],
                command_transport=command_transport,
            )
            existing = loaded.get("record") if loaded.get("status") == "ok" else None
            if (
                isinstance(existing, dict)
                and existing.get("collaborationId") == existing_id
                and existing.get("v") == 2
                and existing.get("ownerEmail") == thread["ownerEmail"]
                and existing.get("workspaceId") == thread["workspaceId"]
                and existing.get("mailboxId") == thread["mailboxId"]
                and existing.get("sourceRef") == thread["sourceRef"]
                and existing.get("ownerUserId") == thread.get("ownerUserId")
                and existing.get("ownerDisplayName")
                == thread.get("ownerDisplayName")
                and existing.get("participants") == thread.get("participants")
            ):
                return _V2RecordResult(existing, created=False)
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        if result.get("status") == "conflict":
            return {"status": "conflict", "error": {"code": "stale_thread"}}
        if result.get("status") in {"source_pointer_conflict", "malformed"}:
            return {"status": "conflict", "error": {"code": "source_pointer_conflict"}}
        return result


    _CREATE_V2_THREAD_WITH_GUEST_LUA = _V2_LUA_COMMON + r"""
    if #ARGV[1] > 262144 or #ARGV[2] > 16384
      or not positiveInteger(ARGV[5]) or not timestampSeconds(ARGV[6])
      or not positiveInteger(ARGV[7]) or (ARGV[8] ~= '0' and ARGV[8] ~= '1') then
      return cjson.encode({status='malformed', predicate='argv_shape'})
    end
    local hasPrevious = ARGV[8] == '1'
    if #KEYS ~= (hasPrevious and 8 or 6) then
      return cjson.encode({status='malformed', predicate='key_count'})
    end
    local threadOk, proposedThread = decodeWire(ARGV[1])
    local inviteOk, proposedInvite = decodeWire(ARGV[2])
    local now = integerValue(ARGV[6])
    if not threadOk then return cjson.encode({status='malformed', predicate='thread_decode'}) end
    if not rawTopLevelArray(ARGV[1], 'messages') then return cjson.encode({status='malformed', predicate='thread_messages'}) end
    if not threadValid(proposedThread) then return cjson.encode({status='malformed', predicate='thread_valid'}) end
    if proposedThread.collaborationId ~= ARGV[3] then return cjson.encode({status='malformed', predicate='thread_id_binding'}) end
    if not inviteOk then return cjson.encode({status='malformed', predicate='invite_decode'}) end
    local inviteIsValid, inviteSubpredicate = inviteValid(proposedInvite)
    if not inviteIsValid then
      return cjson.encode({status='malformed', predicate='invite_valid', subpredicate=inviteSubpredicate})
    end
    if proposedInvite.status ~= 'active' then return cjson.encode({status='malformed', predicate='invite_status'}) end
    if proposedInvite.createdAt ~= ARGV[6] then return cjson.encode({status='malformed', predicate='invite_created_at'}) end
    if integerValue(proposedInvite.expiresAt) - now ~= integerValue(ARGV[5]) then return cjson.encode({status='malformed', predicate='invite_ttl'}) end
    if proposedInvite.inviteId ~= ARGV[4] then return cjson.encode({status='malformed', predicate='invite_id_binding'}) end
    if proposedInvite.tokenHash ~= ARGV[10] then return cjson.encode({status='malformed', predicate='invite_token_binding'}) end
    if proposedInvite.ownerEmail ~= proposedThread.ownerEmail then return cjson.encode({status='malformed', predicate='invite_owner_binding'}) end
    if proposedInvite.workspaceId ~= proposedThread.workspaceId then return cjson.encode({status='malformed', predicate='invite_workspace_binding'}) end
    if proposedInvite.mailboxId ~= proposedThread.mailboxId then return cjson.encode({status='malformed', predicate='invite_mailbox_binding'}) end
    if proposedInvite.collaborationId ~= proposedThread.collaborationId then return cjson.encode({status='malformed', predicate='invite_collaboration_binding'}) end
    local currentPointer = redis.call('GET', KEYS[2])
    local previousPointer = hasPrevious and redis.call('GET', KEYS[7]) or nil
    if currentPointer and previousPointer and currentPointer ~= previousPointer then
      return cjson.encode({status='source_pointer_conflict'})
    end
    local pointer = currentPointer or previousPointer
    if pointer then
      if not opaqueId(pointer) then
        return cjson.encode({status='source_pointer_conflict'})
      end
      local targetKey = ARGV[9] .. pointer
      local targetRaw = redis.call('GET', targetKey)
      if targetRaw then
        if redis.call('PTTL', targetKey) <= 0
          or (currentPointer and redis.call('PTTL', KEYS[2]) <= 0)
          or (previousPointer and redis.call('PTTL', KEYS[7]) <= 0)
          or #targetRaw > 262144 or not rawTopLevelArray(targetRaw, 'messages') then
          return cjson.encode({status='source_pointer_conflict'})
        end
        local targetOk, target = decodeWire(targetRaw)
        if not targetOk or not threadValid(target) or target.collaborationId ~= pointer
          or target.ownerEmail ~= proposedThread.ownerEmail
          or target.workspaceId ~= proposedThread.workspaceId
          or target.mailboxId ~= proposedThread.mailboxId
          or not sourceEqual(target.sourceRef, proposedThread.sourceRef) then
          return cjson.encode({status='source_pointer_conflict'})
        end
        return cjson.encode({status='existing', collaborationId=pointer})
      end
    end
    if redis.call('EXISTS', KEYS[1]) == 1
      or redis.call('EXISTS', KEYS[3]) == 1
      or redis.call('EXISTS', KEYS[4]) == 1
      or redis.call('EXISTS', KEYS[5]) == 1
      or redis.call('EXISTS', KEYS[6]) == 1
      or (hasPrevious and redis.call('EXISTS', KEYS[8]) == 1) then
      return cjson.encode({status='conflict'})
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[7])
    redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[7])
    redis.call('SET', KEYS[3], ARGV[2], 'EX', ARGV[5])
    redis.call('SET', KEYS[4], ARGV[4], 'EX', ARGV[5])
    redis.call('SET', KEYS[5], ARGV[2], 'EX', ARGV[5])
    redis.call('SET', KEYS[6], cjson.encode({v='1', inviteIds={ARGV[4]}}), 'EX', ARGV[5])
    if hasPrevious then
      redis.call('DEL', KEYS[7])
      redis.call('DEL', KEYS[8])
    end
    return cjson.encode({status='created'})
    """.strip()


    def _create_v2_thread_with_guest(
        thread_record: dict,
        invite_record: dict,
        *,
        now: int,
        command_transport=None,
    ) -> dict:
        thread = normalize_v2_thread_record(thread_record)
        invite = normalize_v2_invite_record(invite_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        invite_wire = _v2_wire_json(invite, "invite") if invite is not None else None
        if (
            thread is None
            or invite is None
            or thread_wire is None
            or invite_wire is None
            or type(now) is not int
            or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or invite["status"] != "active"
            or invite["createdAt"] != now
            or invite["ownerEmail"] != thread["ownerEmail"]
            or invite["workspaceId"] != thread["workspaceId"]
            or invite["mailboxId"] != thread["mailboxId"]
            or invite["collaborationId"] != thread["collaborationId"]
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        invite_ttl = invite["expiresAt"] - now
        if invite_ttl <= 0:
            return {"status": "expired", "error": {"code": "invite_expired"}}
        hmac_keys = resolve_v2_index_hmac_keys()
        if hmac_keys is None:
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        current_hmac, previous_hmac = hmac_keys
        thread_key = build_v2_thread_key(thread["collaborationId"])
        source_key = build_v2_source_thread_key(
            thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
            hmac_key=current_hmac,
        )
        invite_key = build_v2_invite_key(invite["inviteId"])
        token_key = build_v2_invite_token_key(invite["tokenHash"])
        identity_key = build_v2_thread_invite_key(
            invite["ownerEmail"], invite["collaborationId"], invite.get("invitedEmail"),
            hmac_key=current_hmac,
        )
        external_guest_index_key = build_v2_external_guest_index_key(
            thread["collaborationId"]
        )
        previous_source_key = (
            build_v2_source_thread_key(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                hmac_key=previous_hmac,
            )
            if previous_hmac is not None
            else None
        )
        previous_identity_key = (
            build_v2_thread_invite_key(
                invite["ownerEmail"], invite["collaborationId"],
                invite.get("invitedEmail"), hmac_key=previous_hmac,
            )
            if previous_hmac is not None
            else None
        )
        required_keys = (
            thread_key, source_key, invite_key, token_key, identity_key,
            external_guest_index_key,
        )
        if any(key is None for key in required_keys) or (
            previous_hmac is not None
            and (previous_source_key is None or previous_identity_key is None)
        ):
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        has_previous = (
            previous_source_key is not None
            and previous_identity_key is not None
            and previous_source_key != source_key
            and previous_identity_key != identity_key
        )
        keys = list(required_keys)
        if has_previous:
            keys.extend((previous_source_key, previous_identity_key))
        protocol_failure_observer = (
            _new_atomic_guest_store_protocol_failure_observer()
        )
        lua_malformed_observer = _new_atomic_guest_lua_malformed_observer()
        invite_invalid_observer = _new_atomic_guest_invite_invalid_observer()
        result = _v2_eval(
            [
                "EVAL", _CREATE_V2_THREAD_WITH_GUEST_LUA, len(keys), *keys,
                thread_wire, invite_wire, thread["collaborationId"],
                invite["inviteId"], str(invite_ttl), str(now),
                str(V2_THREAD_RETENTION_SECONDS), "1" if has_previous else "0",
                V2_THREAD_KEY_PREFIX, invite["tokenHash"],
            ],
            command_transport,
            response_shapes={
                "created": set(), "existing": {"collaborationId"},
                "conflict": set(), "source_pointer_conflict": set(),
                "malformed": {"predicate"},
            },
            optional_response_fields={"malformed": {"subpredicate"}},
            protocol_failure_observer=protocol_failure_observer,
        )
        if result.get("status") == "created":
            return _V2ThreadInviteCreateResult(thread, invite, True, True)
        if result.get("status") == "existing":
            existing_id = result.get("collaborationId")
            loaded = _load_v2_thread_by_source(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"],
                workspace_id=thread["workspaceId"],
                command_transport=command_transport,
            )
            existing_thread = loaded.get("record") if loaded.get("status") == "ok" else None
            if build_v2_thread_key(existing_id) is None:
                protocol_failure_observer("existing_id")
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            if (
                not isinstance(existing_thread, dict)
                or existing_thread.get("collaborationId") != existing_id
            ):
                protocol_failure_observer("existing_thread_reload")
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            converged_invite = normalize_v2_invite_record(
                {**invite, "collaborationId": existing_id}
            )
            if converged_invite is None:
                protocol_failure_observer("existing_invite_normalization")
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            invitation_result = _create_v2_invite(
                converged_invite, now=now, command_transport=command_transport
            )
            if not isinstance(invitation_result, _V2RecordResult):
                if _is_exact_v2_storage_protocol_failure(invitation_result):
                    protocol_failure_observer("existing_invite_create")
                return invitation_result
            return _V2ThreadInviteCreateResult(
                existing_thread,
                invitation_result.record,
                False,
                invitation_result.created is True,
            )
        if result.get("status") == "conflict":
            return {"status": "conflict", "error": {"code": "invalid_request"}}
        if result.get("status") == "source_pointer_conflict":
            return {
                "status": "conflict",
                "error": {"code": "source_changed"},
            }
        if result.get("status") == "malformed":
            protocol_failure_observer("lua_malformed")
            lua_malformed_observer(result.get("predicate"))
            if result.get("predicate") == "invite_valid":
                invite_invalid_observer(result.get("subpredicate"))
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        return result


    _APPEND_V2_GUEST_REPLY_LUA = _V2_LUA_COMMON + r"""
    if not positiveInteger(ARGV[3]) then return cjson.encode({status='malformed'}) end
    local threadState, raw = readString(KEYS[1], 262144)
    if threadState == 'missing' then return cjson.encode({status='missing'}) end
    if threadState ~= 'ok' or #ARGV[2] > 262144 then return cjson.encode({status='oversized'}) end
    local okCurrent, current = decodeWire(raw)
    local okReplacement, replacement = decodeWire(ARGV[2])
    if not okCurrent or not okReplacement or not rawTopLevelArray(raw, 'messages')
      or not rawTopLevelArray(ARGV[2], 'messages') or not threadValid(current) or not threadValid(replacement) then
      return cjson.encode({status='malformed'})
    end
    local pointer = redis.call('GET', KEYS[2])
    if not pointer or pointer ~= current.collaborationId then return cjson.encode({status='source_pointer_conflict'}) end
    if current.collaborationId ~= ARGV[5] or current.ownerEmail ~= ARGV[6]
      or current.workspaceId ~= ARGV[7] or current.mailboxId ~= ARGV[8] then
      return cjson.encode({status='invalid_scope'})
    end
    if current.collaborationId ~= replacement.collaborationId
      or current.v ~= replacement.v
      or current.ownerEmail ~= replacement.ownerEmail
      or current.workspaceId ~= replacement.workspaceId
      or current.mailboxId ~= replacement.mailboxId
      or current.state ~= replacement.state
      or current.createdAt ~= replacement.createdAt
      or not sourceEqual(current.sourceRef, replacement.sourceRef)
      or not sourceMessageEqual(current.sourceMessage, replacement.sourceMessage)
      or not participantAuthorityEqual(current, replacement) then
      return cjson.encode({status='invalid_scope'})
    end
    if not timestampMilliseconds(ARGV[1]) or current.updatedAt ~= ARGV[1] then
      return cjson.encode({status='stale'})
    end
    if integerValue(replacement.updatedAt) <= integerValue(current.updatedAt) then return cjson.encode({status='nonadvancing'}) end
    if #replacement.messages ~= #current.messages + 1 then return cjson.encode({status='invalid_messages'}) end
    for index = 1, #current.messages do
      if not messageEqual(current.messages[index], replacement.messages[index]) then
        return cjson.encode({status='invalid_messages'})
      end
    end
    local appended = replacement.messages[#replacement.messages]
    if appended.authorKind ~= 'guest' or appended.visibility ~= 'shared'
      or appended.authorDisplayName ~= ARGV[12] or appended.createdAt ~= replacement.updatedAt then
      return cjson.encode({status='invalid_messages'})
    end
    if not timestampSeconds(ARGV[4]) or not timestampSeconds(ARGV[11]) then
      return cjson.encode({status='malformed'})
    end
    local now = integerValue(ARGV[4])
    local inviteState, inviteRaw = readString(KEYS[3], 16384)
    if inviteState == 'missing' then return cjson.encode({status='invite_missing'}) end
    if inviteState ~= 'ok' then return cjson.encode({status='invite_invalid'}) end
    local inviteOk, invite = decodeWire(inviteRaw)
    if not inviteOk or not inviteValid(invite) then return cjson.encode({status='invite_invalid'}) end
    if invite.inviteId ~= ARGV[9] or invite.collaborationId ~= ARGV[5]
      or invite.ownerEmail ~= ARGV[6] or invite.workspaceId ~= ARGV[7]
      or invite.mailboxId ~= ARGV[8] then return cjson.encode({status='invite_invalid'}) end
    if invite.status == 'revoked' then return cjson.encode({status='invite_revoked'}) end
    if invite.status == 'expired' or integerValue(invite.expiresAt) <= now then return cjson.encode({status='invite_expired'}) end
    if invite.status ~= 'exchanged' or invite.activeSessionHash ~= ARGV[10] then
      return cjson.encode({status='invite_revoked'})
    end
    local sessionState, sessionRaw = readString(KEYS[4], 16384)
    if sessionState == 'missing' then return cjson.encode({status='session_missing'}) end
    if sessionState ~= 'ok' then return cjson.encode({status='session_invalid'}) end
    local sessionOk, session = decodeWire(sessionRaw)
    if not sessionOk or not sessionValid(session) then return cjson.encode({status='session_invalid'}) end
    if session.sessionHash ~= ARGV[10] or session.inviteId ~= ARGV[9]
      or session.collaborationId ~= ARGV[5] or session.ownerEmail ~= ARGV[6]
      or session.workspaceId ~= ARGV[7] or session.mailboxId ~= ARGV[8]
      or session.guestDisplayName ~= ARGV[12]
      or session.createdAt ~= invite.exchangedAt
      or session.expiresAt ~= ARGV[11]
      or integerValue(session.expiresAt) > integerValue(invite.expiresAt) then return cjson.encode({status='session_invalid'}) end
    if session.status == 'revoked' or session.status == 'logged_out' then
      return cjson.encode({status='session_revoked'})
    end
    if session.status == 'expired' or integerValue(session.expiresAt) <= now then
      return cjson.encode({status='session_expired'})
    end
    if session.status ~= 'active' or now < integerValue(session.createdAt)
      or now < integerValue(session.lastUsedAt) then
      return cjson.encode({status='session_invalid'})
    end
    if redis.call('PTTL', KEYS[1]) <= 0 or redis.call('PTTL', KEYS[2]) <= 0
      or redis.call('PTTL', KEYS[3]) <= 0 or redis.call('PTTL', KEYS[4]) <= 0 then
      return cjson.encode({status='session_invalid'})
    end
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return cjson.encode({status='saved'})
    """.strip()


    def _append_v2_guest_reply_if_expected(
        thread_record: dict,
        expected_updated_at: int,
        *,
        session_context: object,
        now: int,
        command_transport=None,
    ) -> dict:
        from .guest_session import _is_guest_mutation_capability

        thread = normalize_v2_thread_record(thread_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        if (
            thread is None
            or thread_wire is None
            or type(expected_updated_at) is not int
            or not MIN_V2_TIMESTAMP_MILLISECONDS <= expected_updated_at <= MAX_V2_TIMESTAMP_MILLISECONDS
            or type(now) is not int
            or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or not _is_guest_mutation_capability(session_context)
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}

        thread_key = build_v2_thread_key(thread["collaborationId"])
        hmac_keys = resolve_v2_index_hmac_keys()
        source_key = (
            build_v2_source_thread_key(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"], hmac_key=hmac_keys[0]
            )
            if hmac_keys is not None
            else None
        )
        invite_key = build_v2_invite_key(session_context.invite_id)
        session_key = build_v2_guest_session_key(session_context.session_hash)
        if thread_key is None or source_key is None or invite_key is None or session_key is None:
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        if (
            session_context.collaboration_id != thread["collaborationId"]
            or session_context.owner_email != thread["ownerEmail"]
            or session_context.workspace_id != thread["workspaceId"]
            or session_context.mailbox_id != thread["mailboxId"]
        ):
            return {"status": "forbidden", "error": {"code": "forbidden"}}

        result = _v2_eval(
            [
                "EVAL", _APPEND_V2_GUEST_REPLY_LUA, 4,
                thread_key, source_key, invite_key, session_key,
                str(expected_updated_at),
                thread_wire,
                str(V2_THREAD_RETENTION_SECONDS), str(now),
                thread["collaborationId"], thread["ownerEmail"], thread["workspaceId"],
                thread["mailboxId"], session_context.invite_id, session_context.session_hash,
                str(session_context.expires_at), session_context.guest_display_name,
            ],
            command_transport,
            response_shapes={
                "saved": set(), "missing": set(), "stale": set(), "malformed": set(),
                "invalid_scope": set(), "nonadvancing": set(), "source_pointer_conflict": set(),
                "oversized": set(), "invalid_messages": set(), "invite_missing": set(),
                "invite_invalid": set(), "invite_revoked": set(), "invite_expired": set(),
                "session_missing": set(), "session_invalid": set(), "session_revoked": set(),
                "session_expired": set(),
            },
        )
        status = result.get("status")
        if status == "saved":
            return _V2RecordResult(thread)
        if status == "missing":
            return {"status": "missing", "error": {"code": "collaboration_not_found"}}
        if status in {"stale", "nonadvancing"}:
            return {"status": "conflict", "error": {"code": "stale_thread"}}
        if status in {"invite_revoked", "session_revoked", "invite_missing", "session_missing"}:
            return {"status": "revoked", "error": {"code": "session_revoked"}}
        if status in {"invite_expired", "session_expired"}:
            return {"status": "expired", "error": {"code": "session_expired"}}
        if status == "invalid_scope":
            return {"status": "forbidden", "error": {"code": "forbidden"}}
        if status in {
            "malformed", "source_pointer_conflict", "oversized", "invalid_messages",
            "invite_invalid", "session_invalid",
        }:
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        return result


    def _load_v2_thread(collaboration_id: str, *, command_transport=None) -> dict:
        thread_key = build_v2_thread_key(collaboration_id)
        if thread_key is None:
            return {"status": "missing"}
        result = _v2_read_json(thread_key, "thread", command_transport)
        if result.get("status") != "ok":
            return result
        thread = normalize_v2_thread_record(result.get("record"))
        if thread is None or thread["collaborationId"] != collaboration_id.strip():
            return {"status": "malformed"}
        return _V2RecordResult(thread)


    _LOAD_AND_MIGRATE_V2_SOURCE_LUA = _V2_LUA_COMMON + r"""
    local currentPointer = redis.call('GET', KEYS[1])
    local previousPointer = nil
    if #KEYS == 2 then previousPointer = redis.call('GET', KEYS[2]) end
    if currentPointer and previousPointer and currentPointer ~= previousPointer then
      return cjson.encode({status='conflict'})
    end
    local pointer = currentPointer or previousPointer
    if not pointer then return cjson.encode({status='missing'}) end
    if not opaqueId(pointer) then return cjson.encode({status='conflict'}) end
    local pointerTtl = currentPointer and redis.call('PTTL', KEYS[1]) or redis.call('PTTL', KEYS[2])
    if pointerTtl <= 0 then return cjson.encode({status='conflict'}) end
    local targetKey = ARGV[1] .. pointer
    local targetRaw = redis.call('GET', targetKey)
    if not targetRaw or #targetRaw > 262144 or not rawTopLevelArray(targetRaw, 'messages') then return cjson.encode({status='conflict'}) end
    local targetTtl = redis.call('PTTL', targetKey)
    if targetTtl <= 0 then return cjson.encode({status='conflict'}) end
    local targetOk, target = decodeWire(targetRaw)
    local sourceOk, expectedSource = decodeWire(ARGV[5])
if not targetOk or not sourceOk or not sourceValid(expectedSource)
  or not threadValid(target) or target.collaborationId ~= pointer
      or target.ownerEmail ~= ARGV[2] or target.workspaceId ~= ARGV[3]
      or target.mailboxId ~= ARGV[4] or not sourceEqual(target.sourceRef, expectedSource) then
      return cjson.encode({status='conflict'})
    end
    if previousPointer then
      local previousTtl = redis.call('PTTL', KEYS[2])
      if previousTtl <= 0 then return cjson.encode({status='conflict'}) end
      pointerTtl = math.min(pointerTtl, previousTtl)
      redis.call('PSETEX', KEYS[1], math.min(pointerTtl, targetTtl), pointer)
      redis.call('DEL', KEYS[2])
    end
    return cjson.encode({status='found', collaborationId=pointer})
    """.strip()


    def _load_v2_thread_by_source(
        owner_email: str,
        mailbox_id: str,
        source_ref: dict,
        *,
        workspace_id: str | None = None,
        command_transport=None,
    ) -> dict:
        normalized_owner = normalize_v2_email(owner_email)
        normalized_workspace = (
            normalized_owner
            if workspace_id is None or workspace_id == normalized_owner
            else normalize_v2_workspace_id(workspace_id)
        )
        normalized_source = normalize_v2_source_ref(source_ref)
        if (
            normalized_owner is None
            or normalized_workspace is None
            or not isinstance(mailbox_id, str)
            or not mailbox_id.strip()
            or len(mailbox_id.strip()) > 256
            or normalized_source is None
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        hmac_keys = resolve_v2_index_hmac_keys()
        if hmac_keys is None:
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        current_hmac, previous_hmac = hmac_keys
        source_key = build_v2_source_thread_key(
            normalized_owner, mailbox_id.strip(), normalized_source, hmac_key=current_hmac
        )
        previous_source_key = (
            build_v2_source_thread_key(
                normalized_owner, mailbox_id.strip(), normalized_source, hmac_key=previous_hmac
            )
            if previous_hmac is not None
            else None
        )
        if source_key is None or (previous_hmac is not None and previous_source_key is None):
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        keys = [source_key]
        if previous_source_key is not None and previous_source_key != source_key:
            keys.append(previous_source_key)
        pointer = _v2_eval(
            [
                "EVAL", _LOAD_AND_MIGRATE_V2_SOURCE_LUA, len(keys), *keys,
                V2_THREAD_KEY_PREFIX, normalized_owner, normalized_workspace,
                mailbox_id.strip(),
                json.dumps(normalized_source, separators=(",", ":"), sort_keys=True),
            ],
            command_transport,
            response_shapes={"found": {"collaborationId"}, "missing": set(), "conflict": set()},
        )
        if pointer.get("status") == "missing":
            return {"status": "missing", "error": {"code": "collaboration_not_found"}}
        if pointer.get("status") == "conflict":
            return {"status": "malformed", "error": {"code": "source_pointer_conflict"}}
        if pointer.get("status") != "found":
            return pointer
        collaboration_id = pointer.get("collaborationId")
        if not isinstance(collaboration_id, str) or not collaboration_id:
            return {"status": "missing", "error": {"code": "collaboration_not_found"}}
        loaded = _load_v2_thread(collaboration_id, command_transport=command_transport)
        thread = loaded.get("record") if loaded.get("status") == "ok" else None
        if (
            not isinstance(thread, dict)
            or thread.get("ownerEmail") != normalized_owner
            or thread.get("workspaceId") != normalized_workspace
            or thread.get("mailboxId") != mailbox_id.strip()
            or thread.get("sourceRef") != normalized_source
        ):
            return loaded if loaded.get("status") != "ok" else {"status": "malformed"}
        return loaded


    _SAVE_V2_THREAD_CAS_LUA = _V2_LUA_COMMON + r"""
    if not positiveInteger(ARGV[3]) then return cjson.encode({status='malformed'}) end
    local threadState, raw = readString(KEYS[1], 262144)
    if threadState == 'missing' then return cjson.encode({status='missing'}) end
    if threadState ~= 'ok' or #ARGV[2] > 262144 then return cjson.encode({status='oversized'}) end
    local okCurrent, current = decodeWire(raw)
    local okReplacement, replacement = decodeWire(ARGV[2])
    if not okCurrent or not okReplacement or not rawTopLevelArray(raw, 'messages')
      or not rawTopLevelArray(ARGV[2], 'messages') or not threadValid(current) or not threadValid(replacement) then
      return cjson.encode({status='malformed'})
    end
    local pointer = redis.call('GET', KEYS[2])
    if not pointer or pointer ~= current.collaborationId then return cjson.encode({status='source_pointer_conflict'}) end
    if current.collaborationId ~= replacement.collaborationId
      or current.v ~= replacement.v
      or current.ownerEmail ~= replacement.ownerEmail
      or current.workspaceId ~= replacement.workspaceId
      or current.mailboxId ~= replacement.mailboxId
      or current.createdAt ~= replacement.createdAt
      or not sourceEqual(current.sourceRef, replacement.sourceRef)
      or not sourceMessageEqual(current.sourceMessage, replacement.sourceMessage)
      or not participantAuthorityEqual(current, replacement) then
      return cjson.encode({status='invalid_scope'})
    end
    if not timestampMilliseconds(ARGV[1]) or current.updatedAt ~= ARGV[1] then
      return cjson.encode({status='stale'})
    end
    if integerValue(replacement.updatedAt) <= integerValue(current.updatedAt) then
      return cjson.encode({status='nonadvancing'})
    end
    if #replacement.messages ~= #current.messages + 1 then return cjson.encode({status='invalid_messages'}) end
    for index = 1, #current.messages do
      if not messageEqual(current.messages[index], replacement.messages[index]) then
        return cjson.encode({status='invalid_messages'})
      end
    end
    if redis.call('PTTL', KEYS[1]) <= 0 or redis.call('PTTL', KEYS[2]) <= 0 then
      return cjson.encode({status='malformed'})
    end
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return cjson.encode({status='saved'})
    """.strip()


    def _save_v2_thread_if_expected(
        thread_record: dict,
        expected_updated_at: int,
        *,
        command_transport=None,
    ) -> dict:
        thread = normalize_v2_thread_record(thread_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        if thread is None or thread_wire is None or type(expected_updated_at) is not int or not MIN_V2_TIMESTAMP_MILLISECONDS <= expected_updated_at <= MAX_V2_TIMESTAMP_MILLISECONDS:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        thread_key = build_v2_thread_key(thread["collaborationId"])
        hmac_keys = resolve_v2_index_hmac_keys()
        source_key = (
            build_v2_source_thread_key(
                thread["ownerEmail"], thread["mailboxId"], thread["sourceRef"], hmac_key=hmac_keys[0]
            )
            if hmac_keys is not None
            else None
        )
        if thread_key is None or source_key is None:
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        result = _v2_eval(
            [
                "EVAL", _SAVE_V2_THREAD_CAS_LUA, 2,
                thread_key, source_key, str(expected_updated_at),
                thread_wire,
                str(V2_THREAD_RETENTION_SECONDS),
            ],
            command_transport,
            response_shapes={
                "saved": set(), "missing": set(), "stale": set(),
                "malformed": set(), "invalid_scope": set(), "nonadvancing": set(),
                "source_pointer_conflict": set(), "oversized": set(), "invalid_messages": set(),
            },
        )
        if result.get("status") == "saved":
            return _V2RecordResult(thread)
        if result.get("status") == "missing":
            return {"status": "missing", "error": {"code": "collaboration_not_found"}}
        if result.get("status") in {"stale", "nonadvancing"}:
            return {"status": "conflict", "error": {"code": "stale_thread"}}
        if result.get("status") in {"malformed", "invalid_scope", "source_pointer_conflict", "oversized", "invalid_messages"}:
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        return result


    _SAVE_V2_PARTICIPANTS_CAS_LUA = _V2_LUA_COMMON + r"""
    local function findParticipant(values, userId)
      if type(values) ~= 'table' then return nil end
      for index = 1, #values do
        if values[index].userId == userId then return values[index] end
      end
      return nil
    end
    if not positiveInteger(ARGV[3]) or not canonicalUserId(ARGV[4])
      or not canonicalUserId(ARGV[5]) or not displayString(ARGV[6], 256, false)
      or not membershipRef(ARGV[7]) or not displayString(ARGV[8], 256, false) then
      return cjson.encode({status='malformed'})
    end
    local threadState, raw = readString(KEYS[1], 262144)
    if threadState == 'missing' then return cjson.encode({status='missing'}) end
    if threadState ~= 'ok' or #ARGV[2] > 262144 then return cjson.encode({status='malformed'}) end
    local currentOk, current = decodeWire(raw)
    local replacementOk, replacement = decodeWire(ARGV[2])
    if not currentOk or not replacementOk or not rawTopLevelArray(raw, 'messages')
      or not rawTopLevelArray(ARGV[2], 'messages') or not threadValid(current)
      or not threadValid(replacement) then return cjson.encode({status='malformed'}) end
    if not timestampMilliseconds(ARGV[1]) or current.updatedAt ~= ARGV[1] then
      return cjson.encode({status='stale'})
    end
    if replacement.ownerUserId ~= ARGV[5] or replacement.ownerDisplayName ~= ARGV[6]
      or replacement.ownerUserId == ARGV[4] then return cjson.encode({status='invalid_participants'}) end
    if current.ownerUserId ~= nil and (
      current.ownerUserId ~= replacement.ownerUserId
      or current.ownerDisplayName ~= replacement.ownerDisplayName
    ) then return cjson.encode({status='invalid_scope'}) end
    if current.collaborationId ~= replacement.collaborationId
      or current.v ~= replacement.v or current.ownerEmail ~= replacement.ownerEmail
      or current.workspaceId ~= replacement.workspaceId
      or current.mailboxId ~= replacement.mailboxId or current.state ~= replacement.state
      or current.createdAt ~= replacement.createdAt
      or not sourceEqual(current.sourceRef, replacement.sourceRef)
      or not sourceMessageEqual(current.sourceMessage, replacement.sourceMessage)
      or not messagesEqual(current.messages, replacement.messages) then
      return cjson.encode({status='invalid_scope'})
    end
    local currentParticipants = current.participants or {}
    local currentTarget = findParticipant(currentParticipants, ARGV[4])
    local replacementTarget = findParticipant(replacement.participants, ARGV[4])
    if not replacementTarget or replacementTarget.membershipRef ~= ARGV[7]
      or replacementTarget.displayName ~= ARGV[8] then
      return cjson.encode({status='invalid_participants'})
    end
    if (currentTarget and #replacement.participants ~= #currentParticipants)
      or (not currentTarget and #replacement.participants ~= #currentParticipants + 1) then
      return cjson.encode({status='invalid_participants'})
    end
    for index = 1, #currentParticipants do
      local participant = currentParticipants[index]
      if participant.userId ~= ARGV[4] then
        local retained = findParticipant(replacement.participants, participant.userId)
        if not retained or not participantEqual(participant, retained) then
          return cjson.encode({status='invalid_participants'})
        end
      end
    end
    for index = 1, #replacement.participants do
      local participant = replacement.participants[index]
      if participant.userId ~= ARGV[4] then
        local prior = findParticipant(currentParticipants, participant.userId)
        if not prior or not participantEqual(participant, prior) then
          return cjson.encode({status='invalid_participants'})
        end
      end
    end
    local pointer = redis.call('GET', KEYS[2])
    if not pointer or pointer ~= current.collaborationId then
      return cjson.encode({status='source_pointer_conflict'})
    end
    if integerValue(replacement.updatedAt) <= integerValue(current.updatedAt) then
      return cjson.encode({status='nonadvancing'})
    end
    if redis.call('PTTL', KEYS[1]) <= 0 or redis.call('PTTL', KEYS[2]) <= 0 then
      return cjson.encode({status='malformed'})
    end
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return cjson.encode({status='saved'})
    """.strip()


    def _save_v2_participants_if_expected(
        thread_record: dict,
        expected_updated_at: int,
        *,
        participant_user_id: str,
        command_transport=None,
    ) -> dict:
        thread = normalize_v2_thread_record(thread_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        canonical_participant_id = normalize_v2_user_id(participant_user_id)
        participant = next(
            (
                entry
                for entry in thread.get("participants", [])
                if entry["userId"] == canonical_participant_id
            ),
            None,
        ) if thread is not None else None
        if (
            thread is None
            or thread_wire is None
            or type(expected_updated_at) is not int
            or not MIN_V2_TIMESTAMP_MILLISECONDS
            <= expected_updated_at
            <= MAX_V2_TIMESTAMP_MILLISECONDS
            or canonical_participant_id is None
            or type(participant) is not dict
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        thread_key = build_v2_thread_key(thread["collaborationId"])
        hmac_keys = resolve_v2_index_hmac_keys()
        source_key = (
            build_v2_source_thread_key(
                thread["ownerEmail"],
                thread["mailboxId"],
                thread["sourceRef"],
                hmac_key=hmac_keys[0],
            )
            if hmac_keys is not None
            else None
        )
        if thread_key is None or source_key is None:
            return {
                "status": "unavailable",
                "error": {"code": "index_hmac_unavailable"},
            }
        result = _v2_eval(
            [
                "EVAL",
                _SAVE_V2_PARTICIPANTS_CAS_LUA,
                2,
                thread_key,
                source_key,
                str(expected_updated_at),
                thread_wire,
                str(V2_THREAD_RETENTION_SECONDS),
                canonical_participant_id,
                thread["ownerUserId"],
                thread["ownerDisplayName"],
                participant["membershipRef"],
                participant["displayName"],
            ],
            command_transport,
            response_shapes={
                "saved": set(),
                "missing": set(),
                "stale": set(),
                "nonadvancing": set(),
                "malformed": set(),
                "invalid_scope": set(),
                "invalid_participants": set(),
                "source_pointer_conflict": set(),
            },
        )
        if result.get("status") == "saved":
            return _V2RecordResult(thread)
        if result.get("status") == "missing":
            return {
                "status": "missing",
                "error": {"code": "collaboration_not_found"},
            }
        if result.get("status") in {"stale", "nonadvancing"}:
            return {"status": "conflict", "error": {"code": "stale_thread"}}
        if result.get("status") in {
            "malformed",
            "invalid_scope",
            "invalid_participants",
            "source_pointer_conflict",
        }:
            return {
                "status": "malformed",
                "error": {"code": "storage_protocol_error"},
            }
        return result


    _APPEND_V2_OWNER_IDEMPOTENT_LUA = _V2_LUA_COMMON + r"""
    local IDEMPOTENCY_RECORD_MAX = 1024
    local RETENTION_MAX = 15552000

    local function fingerprintValid(value)
      return type(value) == 'string' and #value == 64
        and string.match(value, '^[0-9a-f]+$') ~= nil
    end

    local function idempotencyRecordValid(value)
      return type(value) == 'table' and keyCount(value) == 6
        and value.v == '1' and exactInteger(value.v)
        and fingerprintValid(value.fingerprint)
        and opaqueId(value.collaborationId)
        and (value.action == 'reply' or value.action == 'internal_note')
        and opaqueId(value.messageId)
        and timestampMilliseconds(value.updatedAt)
    end

    local function requestedVisibility(action)
      if action == 'reply' then return 'shared' end
      if action == 'internal_note' then return 'internal' end
      return nil
    end

    local threadRetention = integerValue(ARGV[3])
    local idempotencyRetention = integerValue(ARGV[4])
    if not threadRetention or threadRetention <= 0 or threadRetention > RETENTION_MAX
      or not idempotencyRetention or idempotencyRetention <= 0
      or idempotencyRetention > threadRetention
      or not fingerprintValid(ARGV[5]) or not opaqueId(ARGV[7])
      or not canonicalEmail(ARGV[8]) or not canonicalWorkspaceId(ARGV[9])
      or not mailboxId(ARGV[10]) or not requestedVisibility(ARGV[11])
      or ARGV[12] ~= requestedVisibility(ARGV[11])
      or not displayString(ARGV[13], 256, false)
      or not freeText(ARGV[14], 16384)
      or (ARGV[15] ~= 'owner' and ARGV[15] ~= 'internal') then
      return cjson.encode({status='malformed'})
    end

    local threadState, currentRaw = readString(KEYS[1], 262144)
    if threadState == 'missing' then return cjson.encode({status='missing'}) end
    if threadState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local currentOk, current = decodeWire(currentRaw)
    if not currentOk or not rawTopLevelArray(currentRaw, 'messages')
      or not threadValid(current) then return cjson.encode({status='malformed'}) end
    if current.collaborationId ~= ARGV[7] or current.ownerEmail ~= ARGV[8]
      or current.workspaceId ~= ARGV[9] or current.mailboxId ~= ARGV[10] then
      return cjson.encode({status='invalid_scope'})
    end

    local pointerState, pointer = readString(KEYS[2], 128)
    if pointerState ~= 'ok' or pointer ~= current.collaborationId then
      return cjson.encode({status='source_pointer_conflict'})
    end
    if redis.call('PTTL', KEYS[1]) <= 0 or redis.call('PTTL', KEYS[2]) <= 0 then
      return cjson.encode({status='malformed'})
    end

    local currentIdState, currentIdRaw = readString(KEYS[3], IDEMPOTENCY_RECORD_MAX)
    if currentIdState == 'invalid' then return cjson.encode({status='idempotency_malformed'}) end
    local previousIdState, previousIdRaw = 'missing', nil
    if #KEYS == 4 then
      previousIdState, previousIdRaw = readString(KEYS[4], IDEMPOTENCY_RECORD_MAX)
      if previousIdState == 'invalid' then return cjson.encode({status='idempotency_malformed'}) end
    end
    if currentIdRaw and previousIdRaw and currentIdRaw ~= previousIdRaw then
      return cjson.encode({status='idempotency_malformed'})
    end
    local idempotencyRaw = currentIdRaw or previousIdRaw
    if idempotencyRaw then
      if (currentIdRaw and redis.call('PTTL', KEYS[3]) <= 0)
        or (previousIdRaw and redis.call('PTTL', KEYS[4]) <= 0) then
        return cjson.encode({status='idempotency_malformed'})
      end
      local recordOk, record = decodeWire(idempotencyRaw)
      if not recordOk or not idempotencyRecordValid(record) then
        return cjson.encode({status='idempotency_malformed'})
      end
      if record.fingerprint ~= ARGV[5] then
        return cjson.encode({status='idempotency_conflict'})
      end
      if record.collaborationId ~= ARGV[7] or record.action ~= ARGV[11] then
        return cjson.encode({status='idempotency_malformed'})
      end
      local matched = nil
      local matchCount = 0
      for _, message in ipairs(current.messages) do
        if message.id == record.messageId then
          matched = message
          matchCount = matchCount + 1
        end
      end
      if matchCount ~= 1 or matched.authorKind ~= ARGV[15]
        or matched.authorDisplayName ~= ARGV[13] or matched.text ~= ARGV[14]
        or matched.visibility ~= ARGV[12] or matched.createdAt ~= record.updatedAt
        or integerValue(current.updatedAt) < integerValue(record.updatedAt) then
        return cjson.encode({status='idempotency_malformed'})
      end
      if not currentIdRaw and previousIdRaw then
        local remaining = redis.call('PTTL', KEYS[4])
        local threadRemaining = redis.call('PTTL', KEYS[1])
        local sourceRemaining = redis.call('PTTL', KEYS[2])
        if remaining <= 0 or threadRemaining <= 0 or sourceRemaining <= 0 then
          return cjson.encode({status='idempotency_malformed'})
        end
        if threadRemaining < remaining then remaining = threadRemaining end
        if sourceRemaining < remaining then remaining = sourceRemaining end
        redis.call('PSETEX', KEYS[3], tostring(remaining), previousIdRaw)
        redis.call('DEL', KEYS[4])
      end
      return cjson.encode({status='recovered', message=matched, updatedAt=record.updatedAt})
    end

    if #ARGV[2] > 262144 or #ARGV[6] > IDEMPOTENCY_RECORD_MAX
      or not timestampMilliseconds(ARGV[1]) then
      return cjson.encode({status='malformed'})
    end
    local replacementOk, replacement = decodeWire(ARGV[2])
    local recordOk, record = decodeWire(ARGV[6])
    if not replacementOk or not recordOk
      or not rawTopLevelArray(ARGV[2], 'messages')
      or not threadValid(replacement) or not idempotencyRecordValid(record) then
      return cjson.encode({status='malformed'})
    end
    if current.collaborationId ~= replacement.collaborationId
      or current.v ~= replacement.v or current.ownerEmail ~= replacement.ownerEmail
      or current.workspaceId ~= replacement.workspaceId
      or current.mailboxId ~= replacement.mailboxId
      or current.createdAt ~= replacement.createdAt
      or not sourceEqual(current.sourceRef, replacement.sourceRef)
      or not sourceMessageEqual(current.sourceMessage, replacement.sourceMessage)
      or not participantAuthorityEqual(current, replacement) then
      return cjson.encode({status='invalid_scope'})
    end
    if current.updatedAt ~= ARGV[1] then return cjson.encode({status='stale'}) end
    if integerValue(replacement.updatedAt) <= integerValue(current.updatedAt) then
      return cjson.encode({status='nonadvancing'})
    end
    if #replacement.messages ~= #current.messages + 1 then
      return cjson.encode({status='invalid_messages'})
    end
    for index = 1, #current.messages do
      if not messageEqual(current.messages[index], replacement.messages[index]) then
        return cjson.encode({status='invalid_messages'})
      end
    end
    local appended = replacement.messages[#replacement.messages]
    if appended.authorKind ~= ARGV[15] or appended.authorDisplayName ~= ARGV[13]
      or appended.text ~= ARGV[14] or appended.visibility ~= ARGV[12]
      or appended.createdAt ~= replacement.updatedAt
      or record.fingerprint ~= ARGV[5] or record.collaborationId ~= ARGV[7]
      or record.action ~= ARGV[11] or record.messageId ~= appended.id
      or record.updatedAt ~= appended.createdAt then
      return cjson.encode({status='idempotency_malformed'})
    end

    redis.call('PSETEX', KEYS[3], tostring(idempotencyRetention * 1000), ARGV[6])
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return cjson.encode({status='saved', message=appended, updatedAt=record.updatedAt})
    """.strip()


    def _owner_append_message_from_wire(
        value: object,
        updated_at: object,
    ) -> tuple[dict, int] | None:
        if (
            type(value) is not dict
            or set(value)
            != {
                "id",
                "authorKind",
                "authorDisplayName",
                "text",
                "visibility",
                "createdAt",
            }
            or type(updated_at) is not str
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", updated_at)
            or type(value.get("createdAt")) is not str
            or value.get("createdAt") != updated_at
        ):
            return None
        parsed_updated_at = int(updated_at)
        message = normalize_v2_message_record(
            {**value, "createdAt": parsed_updated_at}
        )
        if message is None or message["createdAt"] != parsed_updated_at:
            return None
        return message, parsed_updated_at


    def _append_v2_owner_message_idempotently(
        thread_record: dict,
        expected_updated_at: int,
        *,
        idempotency_key: str,
        fingerprint: str,
        action: str,
        author_kind: str = "owner",
        command_transport=None,
    ) -> dict:
        thread = normalize_v2_thread_record(thread_record)
        thread_wire = _v2_wire_json(thread, "thread") if thread is not None else None
        canonical_key = normalize_v2_owner_idempotency_key(idempotency_key)
        if (
            thread is None
            or thread_wire is None
            or type(expected_updated_at) is not int
            or not MIN_V2_TIMESTAMP_MILLISECONDS
            <= expected_updated_at
            <= MAX_V2_TIMESTAMP_MILLISECONDS
            or canonical_key is None
            or type(fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or action not in {"reply", "internal_note"}
            or author_kind not in {"owner", "internal"}
            or not thread["messages"]
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        appended = thread["messages"][-1]
        expected_visibility = "shared" if action == "reply" else "internal"
        if (
            appended["authorKind"] != author_kind
            or appended["visibility"] != expected_visibility
            or appended["createdAt"] != thread["updatedAt"]
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}

        thread_key = build_v2_thread_key(thread["collaborationId"])
        hmac_keys = resolve_v2_index_hmac_keys()
        if hmac_keys is None:
            return {
                "status": "unavailable",
                "error": {"code": "index_hmac_unavailable"},
            }
        current_hmac, previous_hmac = hmac_keys
        source_key = build_v2_source_thread_key(
            thread["ownerEmail"],
            thread["mailboxId"],
            thread["sourceRef"],
            hmac_key=current_hmac,
        )
        current_idempotency_key = build_v2_owner_idempotency_key(
            canonical_key,
            hmac_key=current_hmac,
        )
        previous_idempotency_key = (
            build_v2_owner_idempotency_key(
                canonical_key,
                hmac_key=previous_hmac,
            )
            if previous_hmac is not None
            else None
        )
        if (
            thread_key is None
            or source_key is None
            or current_idempotency_key is None
            or (previous_hmac is not None and previous_idempotency_key is None)
        ):
            return {
                "status": "unavailable",
                "error": {"code": "index_hmac_unavailable"},
            }

        idempotency_record = json.dumps(
            {
                "action": action,
                "collaborationId": thread["collaborationId"],
                "fingerprint": fingerprint,
                "messageId": appended["id"],
                "updatedAt": str(appended["createdAt"]),
                "v": "1",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        keys = [thread_key, source_key, current_idempotency_key]
        if (
            previous_idempotency_key is not None
            and previous_idempotency_key != current_idempotency_key
        ):
            keys.append(previous_idempotency_key)
        result = _v2_eval(
            [
                "EVAL",
                _APPEND_V2_OWNER_IDEMPOTENT_LUA,
                len(keys),
                *keys,
                str(expected_updated_at),
                thread_wire,
                str(V2_THREAD_RETENTION_SECONDS),
                str(V2_OWNER_IDEMPOTENCY_RETENTION_SECONDS),
                fingerprint,
                idempotency_record,
                thread["collaborationId"],
                thread["ownerEmail"],
                thread["workspaceId"],
                thread["mailboxId"],
                action,
                expected_visibility,
                appended["authorDisplayName"],
                appended["text"],
                author_kind,
            ],
            command_transport,
            response_shapes={
                "saved": {"message", "updatedAt"},
                "recovered": {"message", "updatedAt"},
                "missing": set(),
                "stale": set(),
                "malformed": set(),
                "invalid_scope": set(),
                "nonadvancing": set(),
                "source_pointer_conflict": set(),
                "invalid_messages": set(),
                "idempotency_malformed": set(),
                "idempotency_conflict": set(),
            },
        )
        if result.get("status") in {"saved", "recovered"}:
            parsed = _owner_append_message_from_wire(
                result.get("message"),
                result.get("updatedAt"),
            )
            if parsed is None:
                return {
                    "status": "malformed",
                    "error": {"code": "storage_protocol_error"},
                }
            message, updated_at = parsed
            return _V2OwnerAppendResult(
                message,
                updated_at,
                recovered=result["status"] == "recovered",
            )
        if result.get("status") == "missing":
            return {
                "status": "missing",
                "error": {"code": "collaboration_not_found"},
            }
        if result.get("status") in {"stale", "nonadvancing"}:
            return {"status": "conflict", "error": {"code": "stale_thread"}}
        if result.get("status") == "idempotency_conflict":
            return {
                "status": "conflict",
                "error": {"code": "idempotency_conflict"},
            }
        if result.get("status") in {
            "malformed",
            "invalid_scope",
            "source_pointer_conflict",
            "invalid_messages",
            "idempotency_malformed",
        }:
            return {
                "status": "malformed",
                "error": {"code": "storage_protocol_error"},
            }
        return result


    _CREATE_V2_INVITE_LUA = _V2_LUA_COMMON + r"""
    local function nullableEqual(a, b)
      if a == JSON_NULL then a = nil end
      if b == JSON_NULL then b = nil end
      return a == b
    end
    local function inviteLinkEqual(a, b)
      return a.v == b.v and a.inviteId == b.inviteId and a.tokenHash == b.tokenHash
        and a.ownerEmail == b.ownerEmail and a.workspaceId == b.workspaceId
        and a.mailboxId == b.mailboxId and a.collaborationId == b.collaborationId
        and nullableEqual(a.invitedEmail, b.invitedEmail)
        and a.identityAssurance == b.identityAssurance
        and a.allowedActions[1] == b.allowedActions[1] and a.allowedActions[2] == b.allowedActions[2]
        and a.visibility == b.visibility and a.createdBy.ownerEmail == b.createdBy.ownerEmail
        and a.createdBy.displayName == b.createdBy.displayName and a.createdAt == b.createdAt
        and a.expiresAt == b.expiresAt and a.status == b.status
        and nullableEqual(a.exchangedAt, b.exchangedAt) and a.exchangeCount == b.exchangeCount
        and nullableEqual(a.revokedAt, b.revokedAt) and nullableEqual(a.revokedBy, b.revokedBy)
        and nullableEqual(a.activeSessionHash, b.activeSessionHash)
    end
    local function inviteMatchesRequested(existing, proposed)
      return existing.ownerEmail == proposed.ownerEmail and existing.workspaceId == proposed.workspaceId
        and existing.mailboxId == proposed.mailboxId and existing.collaborationId == proposed.collaborationId
        and nullableEqual(existing.invitedEmail, proposed.invitedEmail)
        and existing.identityAssurance == proposed.identityAssurance
        and existing.allowedActions[1] == proposed.allowedActions[1]
        and existing.allowedActions[2] == proposed.allowedActions[2]
        and existing.visibility == proposed.visibility and existing.createdBy.ownerEmail == proposed.ownerEmail
    end
    local function loadGuestIndex(indexKey, proposed, now, invitePrefix, maximum)
      local state, raw = readString(indexKey, 4096)
      if state == 'missing' then return {}, 0, nil end
      if state ~= 'ok' then return nil, nil, 'conflict' end
      local ok, index = pcall(cjson.decode, raw)
      if not ok or type(index) ~= 'table' or keyCount(index) ~= 2
        or index.v ~= '1' or type(index.inviteIds) ~= 'table'
        or #index.inviteIds > maximum or keyCount(index.inviteIds) ~= #index.inviteIds then
        return nil, nil, 'conflict'
      end
      local retained = {}
      local maximumPttl = 0
      local previous = nil
      for position = 1, #index.inviteIds do
        local inviteId = index.inviteIds[position]
        if not opaqueId(inviteId) or (previous and previous >= inviteId) then
          return nil, nil, 'conflict'
        end
        previous = inviteId
        local inviteState, inviteRaw = readString(invitePrefix .. inviteId, 16384)
        if inviteState == 'ok' then
          local inviteOk, invite = decodeWire(inviteRaw)
          local invitePttl = redis.call('PTTL', invitePrefix .. inviteId)
          if not inviteOk or not inviteValid(invite) or invite.inviteId ~= inviteId
            or invite.ownerEmail ~= proposed.ownerEmail
            or invite.workspaceId ~= proposed.workspaceId
            or invite.mailboxId ~= proposed.mailboxId
            or invite.collaborationId ~= proposed.collaborationId
            or invitePttl <= 0 then
            return nil, nil, 'conflict'
          end
          if integerValue(invite.expiresAt) > now then
            table.insert(retained, inviteId)
            maximumPttl = math.max(maximumPttl, invitePttl)
          end
        elseif inviteState ~= 'missing' then
          return nil, nil, 'conflict'
        end
      end
      return retained, maximumPttl, nil
    end
    local function addGuestReference(inviteIds, inviteId, maximum)
      for position = 1, #inviteIds do
        if inviteIds[position] == inviteId then return true end
      end
      if #inviteIds >= maximum then return false end
      table.insert(inviteIds, inviteId)
      table.sort(inviteIds)
      return true
    end
    if #ARGV[1] > 16384 or not positiveInteger(ARGV[2]) or not timestampSeconds(ARGV[3]) then
      return cjson.encode({status='malformed'})
    end
    local proposedOk, proposed = decodeWire(ARGV[1])
    local now = integerValue(ARGV[3])
    if not proposedOk or not inviteValid(proposed) or proposed.status ~= 'active' or proposed.createdAt ~= ARGV[3]
      or integerValue(proposed.expiresAt) - now ~= integerValue(ARGV[2])
      or proposed.inviteId ~= ARGV[4] or proposed.tokenHash ~= ARGV[12]
      or (ARGV[7] ~= '0' and ARGV[7] ~= '1') then
      return cjson.encode({status='malformed'})
    end
    local hasPrevious = ARGV[7] == '1'
    local baseKeyCount = hasPrevious and 4 or 3
    local hasExisting = ARGV[8] ~= ''
    local threadKeyIndex = baseKeyCount + (hasExisting and 3 or 1)
    local indexKeyIndex = threadKeyIndex + 1
    if #KEYS ~= indexKeyIndex or not positiveInteger(ARGV[11]) then
      return cjson.encode({status='malformed'})
    end
    local threadState, threadRaw = readString(KEYS[threadKeyIndex], 262144)
    if threadState ~= 'ok' or not rawTopLevelArray(threadRaw, 'messages')
      or redis.call('PTTL', KEYS[threadKeyIndex]) <= 0 then
      return cjson.encode({status='conflict'})
    end
    local threadOk, thread = decodeWire(threadRaw)
    if not threadOk or not threadValid(thread)
      or thread.ownerEmail ~= proposed.ownerEmail
      or thread.workspaceId ~= proposed.workspaceId
      or thread.mailboxId ~= proposed.mailboxId
      or thread.collaborationId ~= proposed.collaborationId then
      return cjson.encode({status='conflict'})
    end
    local guestIds, guestIndexPttl, guestIndexError = loadGuestIndex(
      KEYS[indexKeyIndex], proposed, now, ARGV[10], integerValue(ARGV[11])
    )
    if guestIndexError then return cjson.encode({status=guestIndexError}) end
    local currentState, currentRaw = readString(KEYS[3], 16384)
    local previousRaw = nil
    local previousState = 'missing'
    if hasPrevious then previousState, previousRaw = readString(KEYS[4], 16384) end
    if currentState == 'invalid' or previousState == 'invalid' then
      return cjson.encode({status='conflict'})
    end
    if (currentRaw or '') ~= ARGV[5] or (previousRaw or '') ~= ARGV[6] then
      return cjson.encode({status='retry'})
    end
    local current = nil
    local previous = nil
    if currentRaw then
      local ok
      ok, current = decodeWire(currentRaw)
      if not ok or not inviteValid(current) then return cjson.encode({status='conflict'}) end
    end
    if previousRaw then
      local ok
      ok, previous = decodeWire(previousRaw)
      if not ok or not inviteValid(previous) then return cjson.encode({status='conflict'}) end
    end
    if current and previous and (current.inviteId ~= previous.inviteId or not inviteLinkEqual(current, previous)) then
      return cjson.encode({status='conflict'})
    end
    local existing = current or previous
    if existing then
      local canonicalKeyIndex = baseKeyCount + 1
      local tokenKeyIndex = baseKeyCount + 2
      if existing.status ~= 'active' or integerValue(existing.expiresAt) <= now
        or existing.inviteId ~= ARGV[8] or existing.tokenHash ~= ARGV[9]
        or not inviteMatchesRequested(existing, proposed) then return cjson.encode({status='conflict'}) end
      local canonicalState, canonicalRaw = readString(KEYS[canonicalKeyIndex], 16384)
      local tokenState, tokenPointer = readString(KEYS[tokenKeyIndex], 128)
      if canonicalState ~= 'ok' or tokenState ~= 'ok' then return cjson.encode({status='conflict'}) end
      local canonicalOk, canonical = decodeWire(canonicalRaw)
      if not canonicalOk or not inviteValid(canonical) or canonical.status ~= 'active'
        or integerValue(canonical.expiresAt) <= now or not inviteLinkEqual(existing, canonical)
        or not inviteMatchesRequested(canonical, proposed) or tokenPointer ~= canonical.inviteId then
        return cjson.encode({status='conflict'})
      end
      if KEYS[1] ~= KEYS[canonicalKeyIndex] and redis.call('EXISTS', KEYS[1]) == 1 then
        return cjson.encode({status='conflict'})
      end
      if KEYS[2] ~= KEYS[tokenKeyIndex] and redis.call('EXISTS', KEYS[2]) == 1 then
        return cjson.encode({status='conflict'})
      end
      local absolutePttl = (integerValue(canonical.expiresAt) - now) * 1000
      local canonicalPttl = redis.call('PTTL', KEYS[canonicalKeyIndex])
      local tokenPttl = redis.call('PTTL', KEYS[tokenKeyIndex])
      local currentPttl = current and redis.call('PTTL', KEYS[3]) or nil
      local previousPttl = previous and redis.call('PTTL', KEYS[4]) or nil
      if canonicalPttl <= 0 or tokenPttl <= 0 or tokenPttl > canonicalPttl + 1000
        or canonicalPttl > absolutePttl or tokenPttl > absolutePttl
        or (current and (currentPttl <= 0 or currentPttl > canonicalPttl + 1000 or currentPttl > absolutePttl))
        or (previous and (previousPttl <= 0 or previousPttl > canonicalPttl + 1000 or previousPttl > absolutePttl)) then
        return cjson.encode({status='conflict'})
      end
      if not addGuestReference(guestIds, canonical.inviteId, integerValue(ARGV[11])) then
        return cjson.encode({status='capacity'})
      end
      if previous then
        if not current then
          local migrationPttl = math.min(absolutePttl, canonicalPttl, tokenPttl, previousPttl)
          redis.call('SET', KEYS[3], canonicalRaw, 'PX', math.floor(migrationPttl))
        end
        redis.call('DEL', KEYS[4])
      end
      guestIndexPttl = math.max(guestIndexPttl, canonicalPttl)
      redis.call('SET', KEYS[indexKeyIndex], cjson.encode({v='1', inviteIds=guestIds}), 'PX', math.floor(guestIndexPttl))
      return cjson.encode({status='duplicate', inviteId=existing.inviteId})
    end
    if ARGV[8] ~= '' or ARGV[9] ~= '' then
      return cjson.encode({status='malformed'})
    end
    if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('EXISTS', KEYS[2]) == 1 then
      return cjson.encode({status='conflict'})
    end
    if not addGuestReference(guestIds, proposed.inviteId, integerValue(ARGV[11])) then
      return cjson.encode({status='capacity'})
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    redis.call('SET', KEYS[2], ARGV[4], 'EX', ARGV[2])
    redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
    guestIndexPttl = math.max(guestIndexPttl, integerValue(ARGV[2]) * 1000)
    redis.call('SET', KEYS[indexKeyIndex], cjson.encode({v='1', inviteIds=guestIds}), 'PX', math.floor(guestIndexPttl))
    return cjson.encode({status='created'})
    """.strip()


    _VALIDATE_V2_INVITE_GRAPH_LUA = _V2_LUA_COMMON + r"""
    local function nullableEqual(a, b)
      if a == JSON_NULL then a = nil end
      if b == JSON_NULL then b = nil end
      return a == b
    end
    local function inviteLinkEqual(a, b)
      return a.v == b.v and a.inviteId == b.inviteId and a.tokenHash == b.tokenHash
        and a.ownerEmail == b.ownerEmail and a.workspaceId == b.workspaceId
        and a.mailboxId == b.mailboxId and a.collaborationId == b.collaborationId
        and nullableEqual(a.invitedEmail, b.invitedEmail)
        and a.identityAssurance == b.identityAssurance
        and a.allowedActions[1] == b.allowedActions[1] and a.allowedActions[2] == b.allowedActions[2]
        and a.visibility == b.visibility and a.createdBy.ownerEmail == b.createdBy.ownerEmail
        and a.createdBy.displayName == b.createdBy.displayName and a.createdAt == b.createdAt
        and a.expiresAt == b.expiresAt and a.status == b.status
        and nullableEqual(a.exchangedAt, b.exchangedAt) and a.exchangeCount == b.exchangeCount
        and nullableEqual(a.revokedAt, b.revokedAt) and nullableEqual(a.revokedBy, b.revokedBy)
        and nullableEqual(a.activeSessionHash, b.activeSessionHash)
    end
    local function inviteMatchesRequested(existing, proposed)
      return existing.ownerEmail == proposed.ownerEmail and existing.workspaceId == proposed.workspaceId
        and existing.mailboxId == proposed.mailboxId and existing.collaborationId == proposed.collaborationId
        and nullableEqual(existing.invitedEmail, proposed.invitedEmail)
        and existing.identityAssurance == proposed.identityAssurance
        and existing.allowedActions[1] == proposed.allowedActions[1]
        and existing.allowedActions[2] == proposed.allowedActions[2]
        and existing.visibility == proposed.visibility and existing.createdBy.ownerEmail == proposed.ownerEmail
    end
    if #ARGV[1] > 16384 or #ARGV[2] > 16384 or not timestampSeconds(ARGV[3])
      or (ARGV[6] ~= '0' and ARGV[6] ~= '1') then
      return cjson.encode({status='malformed'})
    end
    local hasPrevious = ARGV[6] == '1'
    local expectedKeyCount = hasPrevious and 4 or 3
    if #KEYS ~= expectedKeyCount then return cjson.encode({status='malformed'}) end
    local canonicalKeyIndex = hasPrevious and 3 or 2
    local tokenKeyIndex = hasPrevious and 4 or 3
    local expectedOk, expected = decodeWire(ARGV[1])
    local proposedOk, proposed = decodeWire(ARGV[2])
    local now = integerValue(ARGV[3])
    if not expectedOk or not proposedOk or not inviteValid(expected) or not inviteValid(proposed)
      or expected.status ~= 'active' or integerValue(expected.expiresAt) <= now
      or expected.inviteId ~= ARGV[4] or expected.tokenHash ~= ARGV[5]
      or not inviteMatchesRequested(expected, proposed) then
      return cjson.encode({status='malformed'})
    end
    local currentState, currentRaw = readString(KEYS[1], 16384)
    local previousState, previousRaw = 'missing', nil
    if hasPrevious then previousState, previousRaw = readString(KEYS[2], 16384) end
    local canonicalState, canonicalRaw = readString(KEYS[canonicalKeyIndex], 16384)
    local tokenState, tokenPointer = readString(KEYS[tokenKeyIndex], 128)
    if currentState ~= 'ok' or previousState ~= 'missing'
      or canonicalState ~= 'ok' or tokenState ~= 'ok' then
      return cjson.encode({status='conflict'})
    end
    local currentOk, current = decodeWire(currentRaw)
    local canonicalOk, canonical = decodeWire(canonicalRaw)
    if not currentOk or not canonicalOk or not inviteValid(current) or not inviteValid(canonical)
      or current.status ~= 'active' or canonical.status ~= 'active'
      or integerValue(current.expiresAt) <= now or integerValue(canonical.expiresAt) <= now
      or not inviteLinkEqual(current, expected) or not inviteLinkEqual(canonical, expected)
      or not inviteLinkEqual(current, canonical) or not inviteMatchesRequested(current, proposed)
      or tokenPointer ~= expected.inviteId then
      return cjson.encode({status='conflict'})
    end
    local absolutePttl = (integerValue(expected.expiresAt) - now) * 1000
    local currentPttl = redis.call('PTTL', KEYS[1])
    local canonicalPttl = redis.call('PTTL', KEYS[canonicalKeyIndex])
    local tokenPttl = redis.call('PTTL', KEYS[tokenKeyIndex])
    if currentPttl <= 0 or canonicalPttl <= 0 or tokenPttl <= 0
      or currentPttl > absolutePttl or canonicalPttl > absolutePttl or tokenPttl > absolutePttl
      or currentPttl > canonicalPttl + 1000 or tokenPttl > canonicalPttl + 1000 then
      return cjson.encode({status='conflict'})
    end
    return cjson.encode({
      status='validated',
      invitation=canonical,
      linkage={
        inviteId=expected.inviteId,
        tokenHash=expected.tokenHash,
        tokenPointer=tokenPointer,
        currentIdentityState='present',
        currentIdentityInviteId=current.inviteId,
        currentIdentityTokenHash=current.tokenHash,
        canonicalInviteId=canonical.inviteId,
        canonicalTokenHash=canonical.tokenHash,
        previousIdentityState=hasPrevious and 'absent' or 'not_configured'
      }
    })
    """.strip()


    def _create_v2_invite(invite_record: dict, *, now: int, command_transport=None) -> dict:
        invite = normalize_v2_invite_record(invite_record)
        invite_wire = _v2_wire_json(invite, "invite") if invite is not None else None
        if invite is None or invite_wire is None or invite["status"] != "active" or invite["createdAt"] != now or type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        ttl = invite["expiresAt"] - now
        if ttl <= 0:
            return {"status": "expired", "error": {"code": "invite_expired"}}
        invited_email = invite.get("invitedEmail")
        invite_key = build_v2_invite_key(invite["inviteId"])
        token_key = build_v2_invite_token_key(invite["tokenHash"])
        thread_key = build_v2_thread_key(invite["collaborationId"])
        external_guest_index_key = build_v2_external_guest_index_key(
            invite["collaborationId"]
        )
        hmac_keys = resolve_v2_index_hmac_keys()
        identity_key = (
            build_v2_thread_invite_key(
                invite["ownerEmail"], invite["collaborationId"], invited_email, hmac_key=hmac_keys[0]
            )
            if hmac_keys is not None
            else None
        )
        previous_identity_key = (
            build_v2_thread_invite_key(
                invite["ownerEmail"], invite["collaborationId"], invited_email, hmac_key=hmac_keys[1]
            )
            if hmac_keys is not None and hmac_keys[1] is not None
            else None
        )
        if (
            invite_key is None
            or token_key is None
            or identity_key is None
            or thread_key is None
            or external_guest_index_key is None
        ):
            return {"status": "unavailable", "error": {"code": "index_hmac_unavailable"}}
        has_previous = previous_identity_key is not None and previous_identity_key != identity_key
        result = None
        expected_existing = None
        canonical_key = None
        canonical_token_key = None
        for _attempt in range(3):
            current_read = _v2_command(["GET", identity_key], command_transport)
            if current_read.get("status") != "ok":
                return current_read
            previous_read = (
                _v2_command(["GET", previous_identity_key], command_transport)
                if has_previous
                else {"status": "ok", "result": None}
            )
            if previous_read.get("status") != "ok":
                return previous_read
            current_raw = current_read.get("result")
            previous_raw = previous_read.get("result")
            if (current_raw is not None and not isinstance(current_raw, str)) or (
                previous_raw is not None and not isinstance(previous_raw, str)
            ):
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}

            keys = [invite_key, token_key, identity_key]
            if has_previous:
                keys.append(previous_identity_key)
            canonical_id = ""
            canonical_token_hash = ""
            existing_raw = current_raw or previous_raw
            expected_existing = None
            canonical_key = None
            canonical_token_key = None
            if existing_raw:
                existing_candidate = _v2_json_from_wire(existing_raw, "invite")
                expected_existing = normalize_v2_invite_record(existing_candidate)
                if expected_existing is not None:
                    candidate_id = existing_candidate.get("inviteId")
                    candidate_token_hash = existing_candidate.get("tokenHash")
                    canonical_key = build_v2_invite_key(candidate_id)
                    canonical_token_key = build_v2_invite_token_key(candidate_token_hash)
                    if canonical_key is not None and canonical_token_key is not None:
                        canonical_id = candidate_id
                        canonical_token_hash = candidate_token_hash
                        keys.extend((canonical_key, canonical_token_key))
            keys.extend((thread_key, external_guest_index_key))

            result = _v2_eval(
                [
                    "EVAL", _CREATE_V2_INVITE_LUA, len(keys), *keys,
                    invite_wire,
                    str(ttl), str(now), invite["inviteId"],
                    current_raw or "", previous_raw or "", "1" if has_previous else "0",
                    canonical_id, canonical_token_hash,
                    V2_INVITE_KEY_PREFIX, str(MAX_V2_EXTERNAL_GUESTS),
                    invite["tokenHash"],
                ],
                command_transport,
                response_shapes={
                    "created": set(), "duplicate": {"inviteId"}, "conflict": set(),
                    "malformed": set(), "retry": set(), "capacity": set(),
                },
            )
            if result.get("status") != "retry":
                break
        if result is None or result.get("status") == "retry":
            return {"status": "conflict", "error": {"code": "stale_invitation"}}
        if result.get("status") == "created":
            return _V2RecordResult(invite, created=True)
        if result.get("status") == "duplicate":
            if (
                expected_existing is None
                or canonical_key is None
                or canonical_token_key is None
                or result.get("inviteId") != expected_existing["inviteId"]
            ):
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            validation_keys = [identity_key]
            if has_previous:
                validation_keys.append(previous_identity_key)
            validation_keys.extend((canonical_key, canonical_token_key))
            expected_existing_wire = _v2_wire_json(expected_existing, "invite")
            if expected_existing_wire is None:
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            validated = _v2_eval(
                [
                    "EVAL", _VALIDATE_V2_INVITE_GRAPH_LUA,
                    len(validation_keys), *validation_keys,
                    expected_existing_wire,
                    invite_wire,
                    str(now), expected_existing["inviteId"], expected_existing["tokenHash"],
                    "1" if has_previous else "0",
                ],
                command_transport,
                response_shapes={
                    "validated": {"invitation", "linkage"},
                    "conflict": set(), "malformed": set(),
                },
            )
            canonical = (
                normalize_v2_invite_record(
                    _v2_record_from_wire(validated.get("invitation"), "invite")
                )
                if validated.get("status") == "validated"
                else None
            )
            expected_linkage = {
                "inviteId": expected_existing["inviteId"],
                "tokenHash": expected_existing["tokenHash"],
                "tokenPointer": expected_existing["inviteId"],
                "currentIdentityState": "present",
                "currentIdentityInviteId": expected_existing["inviteId"],
                "currentIdentityTokenHash": expected_existing["tokenHash"],
                "canonicalInviteId": expected_existing["inviteId"],
                "canonicalTokenHash": expected_existing["tokenHash"],
                "previousIdentityState": "absent" if has_previous else "not_configured",
            }
            if (
                canonical is not None
                and canonical == expected_existing
                and validated.get("linkage") == expected_linkage
            ):
                return _V2RecordResult(canonical, created=False)
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        if result.get("status") == "conflict":
            return {"status": "conflict", "error": {"code": "invalid_request"}}
        if result.get("status") == "capacity":
            return {
                "status": "conflict",
                "error": {"code": "guest_capacity_reached"},
            }
        if result.get("status") == "malformed":
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        return result


    def _v2_invite_runtime_result(invite: dict, now: int) -> dict:
        if invite["status"] == "revoked":
            return {"status": "revoked", "error": {"code": "invite_revoked"}}
        if invite["expiresAt"] <= now or invite["status"] == "expired":
            return {"status": "expired", "error": {"code": "invite_expired"}}
        return _V2RecordResult(invite)


    def _load_v2_invite_by_id(invite_id: str, *, now: int, command_transport=None) -> dict:
        if type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        invite_key = build_v2_invite_key(invite_id)
        if invite_key is None:
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        result = _v2_read_json(invite_key, "invite", command_transport)
        if result.get("status") == "missing":
            return {**result, "error": {"code": "invite_not_found"}}
        if result.get("status") != "ok":
            return result
        invite = normalize_v2_invite_record(result.get("record"))
        if invite is None or invite["inviteId"] != invite_id.strip():
            return {"status": "malformed"}
        return _v2_invite_runtime_result(invite, now)


    def _load_v2_external_guest_records(
        collaboration_id: str,
        *,
        owner_email: str,
        workspace_id: str,
        mailbox_id: str,
        now: int,
        session_normalizer,
        command_transport=None,
    ) -> dict:
        if (
            build_v2_thread_key(collaboration_id) is None
            or normalize_v2_email(owner_email) != owner_email
            or normalize_v2_workspace_id(workspace_id) != workspace_id
            or not isinstance(mailbox_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_id)
            or type(now) is not int
            or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or not callable(session_normalizer)
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        index_key = build_v2_external_guest_index_key(collaboration_id)
        if index_key is None:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        index_result = _v2_command(["GET", index_key], command_transport)
        if index_result.get("status") != "ok":
            return index_result
        raw_index = index_result.get("result")
        if raw_index is None:
            return {"status": "ok", "records": []}
        try:
            if not isinstance(raw_index, str) or len(raw_index.encode("utf-8")) > 4096:
                raise ValueError("invalid external guest index")
            index = _normalize_v2_external_guest_index(
                _strict_json_loads(raw_index, reject_numbers=True)
            )
        except (UnicodeEncodeError, ValueError, json.JSONDecodeError, RecursionError):
            index = None
        if index is None:
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        records: list[dict] = []
        for invite_id in index["inviteIds"]:
            invite_key = build_v2_invite_key(invite_id)
            if invite_key is None:
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            loaded_invite = _v2_read_json(invite_key, "invite", command_transport)
            if loaded_invite.get("status") == "missing":
                continue
            if loaded_invite.get("status") != "ok":
                return loaded_invite
            invite = normalize_v2_invite_record(loaded_invite.get("record"))
            if (
                invite is None
                or invite["inviteId"] != invite_id
                or invite["ownerEmail"] != owner_email
                or invite["workspaceId"] != workspace_id
                or invite["mailboxId"] != mailbox_id
                or invite["collaborationId"] != collaboration_id
            ):
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            session = None
            session_hash = invite.get("activeSessionHash")
            if session_hash is not None:
                session_key = build_v2_guest_session_key(session_hash)
                if session_key is None:
                    return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
                loaded_session = _v2_read_json(session_key, "session", command_transport)
                if loaded_session.get("status") == "missing":
                    exchanged_at = invite.get("exchangedAt")
                    safely_elapsed = (
                        type(exchanged_at) is int
                        and now >= exchanged_at + MAX_V2_GUEST_SESSION_LIFETIME_SECONDS
                    )
                    if invite["status"] != "revoked" and not safely_elapsed:
                        return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
                elif loaded_session.get("status") != "ok":
                    return loaded_session
                else:
                    session = session_normalizer(loaded_session.get("record"))
                    if (
                        session is None
                        or session["sessionHash"] != session_hash
                        or session["inviteId"] != invite_id
                        or session["ownerEmail"] != owner_email
                        or session["workspaceId"] != workspace_id
                        or session["mailboxId"] != mailbox_id
                        or session["collaborationId"] != collaboration_id
                        or session["createdAt"] != invite.get("exchangedAt")
                        or session["expiresAt"] > invite["expiresAt"]
                    ):
                        return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            records.append({"invite": invite, "session": session})
        return {"status": "ok", "records": records}


    def _load_v2_invite_by_token(raw_token: str, *, now: int, command_transport=None) -> dict:
        if type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        token_hash = hash_v2_secret(raw_token)
        if token_hash is None:
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        token_key = build_v2_invite_token_key(token_hash)
        if token_key is None:
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        pointer = _v2_command(["GET", token_key], command_transport)
        if pointer.get("status") != "ok":
            return pointer
        invite_id = pointer.get("result")
        if not isinstance(invite_id, str) or not invite_id:
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        invite_key = build_v2_invite_key(invite_id)
        raw_result = (
            _v2_read_json(invite_key, "invite", command_transport)
            if invite_key is not None
            else {"status": "missing"}
        )
        invite = normalize_v2_invite_record(raw_result.get("record")) if raw_result.get("status") == "ok" else None
        if invite is None or invite["inviteId"] != invite_id or invite["tokenHash"] != token_hash:
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        return _v2_invite_runtime_result(invite, now)


    _EXCHANGE_V2_INVITE_LUA = _V2_LUA_COMMON + r"""
    local pointerState, pointer = readString(KEYS[1], 128)
    if pointerState ~= 'ok' or pointer ~= ARGV[1] then return cjson.encode({status='missing'}) end
    local inviteState, raw = readString(KEYS[2], 16384)
    if inviteState == 'missing' then return cjson.encode({status='missing'}) end
    if inviteState ~= 'ok' or #ARGV[4] > 16384 or not timestampSeconds(ARGV[2])
      or not positiveInteger(ARGV[3]) or not positiveInteger(ARGV[7]) then
      return cjson.encode({status='malformed'})
    end
    local now = integerValue(ARGV[2])
    local inviteOk, invite = decodeWire(raw)
    local sessionOk, session = decodeWire(ARGV[4])
    if not inviteOk or not sessionOk or not inviteValid(invite) or not sessionValid(session) then
      return cjson.encode({status='malformed'})
    end
    if invite.inviteId ~= ARGV[1] or invite.tokenHash ~= ARGV[15]
      or invite.ownerEmail ~= ARGV[8] or invite.workspaceId ~= ARGV[9]
      or invite.mailboxId ~= ARGV[10] or invite.collaborationId ~= ARGV[11]
      or (invite.invitedEmail or '') ~= ARGV[12]
      or invite.createdBy.ownerEmail ~= ARGV[8] then return cjson.encode({status='malformed'}) end
    if invite.status == 'revoked' then return cjson.encode({status='revoked'}) end
    if invite.status == 'expired' or integerValue(invite.expiresAt) <= now then return cjson.encode({status='expired'}) end
    if now < integerValue(invite.createdAt) then return cjson.encode({status='malformed'}) end
    if invite.status ~= 'active' then return cjson.encode({status='exchanged'}) end
    if integerValue(ARGV[3]) ~= integerValue(invite.expiresAt) - now then
      return cjson.encode({status='malformed'})
    end
    if session.inviteId ~= ARGV[1] or session.sessionHash ~= ARGV[5]
      or session.csrfTokenHash ~= ARGV[6] or session.ownerEmail ~= ARGV[8]
      or session.workspaceId ~= ARGV[9] or session.mailboxId ~= ARGV[10]
      or session.collaborationId ~= ARGV[11] or session.status ~= 'active'
      or session.createdAt ~= ARGV[13] or session.lastUsedAt ~= ARGV[13]
      or session.expiresAt ~= ARGV[14] or session.createdAt ~= ARGV[2]
      or integerValue(session.expiresAt) <= now
      or integerValue(session.expiresAt) > integerValue(invite.expiresAt)
      or integerValue(ARGV[7]) ~= integerValue(session.expiresAt) - now then
      return cjson.encode({status='malformed'})
    end
    if redis.call('EXISTS', KEYS[3]) == 1 then return cjson.encode({status='conflict'}) end
    invite.status = 'exchanged'
    invite.exchangedAt = ARGV[2]
    invite.exchangeCount = '1'
    invite.activeSessionHash = ARGV[5]
    if session.createdAt ~= invite.exchangedAt or not inviteValid(invite) then
      return cjson.encode({status='malformed'})
    end
    local tokenPttl = redis.call('PTTL', KEYS[1])
    local invitePttl = redis.call('PTTL', KEYS[2])
    local inviteAbsolutePttl = (integerValue(invite.expiresAt) - now) * 1000
    if tokenPttl <= 0 or invitePttl <= 0 or inviteAbsolutePttl <= 0 then
      return cjson.encode({status='expired'})
    end
    local inviteWritePttl = math.min(tokenPttl, invitePttl, inviteAbsolutePttl)
    local sessionWritePttl = math.min(integerValue(ARGV[7]) * 1000, inviteWritePttl)
    if sessionWritePttl <= 0 then return cjson.encode({status='expired'}) end
    redis.call('SET', KEYS[2], cjson.encode(invite), 'PX', math.floor(inviteWritePttl))
    redis.call('SET', KEYS[3], ARGV[4], 'PX', math.floor(sessionWritePttl))
    return cjson.encode({status='exchanged_ok'})
    """.strip()


    def _atomic_exchange_v2_invite(
        *,
        raw_token: str,
        invite_id: str,
        session_record: dict,
        now: int,
        session_ttl: int,
        command_transport=None,
    ) -> dict:
        token_hash = hash_v2_secret(raw_token)
        session_hash = session_record.get("sessionHash") if isinstance(session_record, dict) else None
        csrf_hash = session_record.get("csrfTokenHash") if isinstance(session_record, dict) else None
        session_wire = (
            _v2_wire_json(session_record, "session")
            if isinstance(session_record, dict)
            else None
        )
        if (
            token_hash is None
            or not isinstance(session_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", session_hash)
            or not isinstance(csrf_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", csrf_hash)
            or session_wire is None
            or type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or type(session_ttl) is not int or session_ttl <= 0
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        invite_read = _load_v2_invite_by_id(invite_id, now=now, command_transport=command_transport)
        if invite_read.get("status") != "ok":
            return invite_read
        invite = invite_read["record"]
        invite_ttl = invite["expiresAt"] - now
        token_key = build_v2_invite_token_key(token_hash)
        invite_key = build_v2_invite_key(invite_id)
        session_key = build_v2_guest_session_key(session_hash)
        if token_key is None or invite_key is None or session_key is None:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        result = _v2_eval(
            [
                "EVAL", _EXCHANGE_V2_INVITE_LUA, 3,
                token_key, invite_key, session_key,
                invite_id, str(now), str(invite_ttl),
                session_wire,
                session_hash, csrf_hash, str(min(session_ttl, invite_ttl)),
                invite["ownerEmail"], invite["workspaceId"], invite["mailboxId"],
                invite["collaborationId"], invite.get("invitedEmail", ""),
                str(session_record.get("createdAt")), str(session_record.get("expiresAt")), token_hash,
            ],
            command_transport,
            response_shapes={
                "exchanged_ok": set(), "missing": set(), "expired": set(),
                "revoked": set(), "exchanged": set(), "conflict": set(), "malformed": set(),
            },
            exchange=True,
        )
        status = result.get("status")
        if status == "exchanged_ok":
            return {"status": "ok"}
        code_by_status = {
            "missing": "invite_not_found", "expired": "invite_expired",
            "revoked": "invite_revoked", "exchanged": "invite_already_exchanged",
            "conflict": "atomic_exchange_unavailable",
            "malformed": "storage_protocol_error",
        }
        if status in code_by_status:
            return {"status": status, "error": {"code": code_by_status[status]}}
        return result


    def _load_v2_guest_session_record(
        raw_session_id: str,
        *,
        normalizer,
        now: int,
        command_transport=None,
    ) -> dict:
        if type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        session_hash = hash_v2_secret(raw_session_id)
        if session_hash is None:
            return {"status": "missing", "error": {"code": "session_not_found"}}
        session_key = build_v2_guest_session_key(session_hash)
        if session_key is None:
            return {"status": "missing", "error": {"code": "session_not_found"}}
        result = _v2_read_json(session_key, "session", command_transport)
        if result.get("status") == "missing":
            return {**result, "error": {"code": "session_not_found"}}
        if result.get("status") != "ok":
            return result
        session = normalizer(result.get("record"))
        if session is None or session.get("sessionHash") != session_hash:
            return {"status": "malformed"}
        if now < session.get("createdAt", 0) or now < session.get("lastUsedAt", 0):
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        if session.get("status") in {"revoked", "logged_out"}:
            return {"status": "revoked", "error": {"code": "session_revoked"}}
        if session.get("status") == "expired" or session.get("expiresAt", 0) <= now:
            return {"status": "expired", "error": {"code": "session_expired"}}
        return _V2RecordResult(session)


    _UPDATE_V2_SESSION_LUA = _V2_LUA_COMMON + r"""
    if type(ARGV[13]) ~= 'string' or #ARGV[13] > 16384 then
      return cjson.encode({status='malformed'})
    end
    local sessionState, raw = readString(KEYS[1], 16384)
    if sessionState == 'missing' then return cjson.encode({status='missing'}) end
    if sessionState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local inviteState, inviteRaw = readString(KEYS[2], 16384)
    if inviteState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local sessionOk, session = decodeWire(raw)
    local inviteOk, invite = decodeWire(inviteRaw)
    local expectedOk, expected = decodeWire(ARGV[13])
    if not timestampSeconds(ARGV[1]) or not timestampSeconds(ARGV[12])
      or not positiveInteger(ARGV[3]) then return cjson.encode({status='malformed'}) end
    local now = integerValue(ARGV[1])
    if not sessionOk or not inviteOk or not expectedOk or not sessionValid(session)
      or not inviteValid(invite) or not sessionValid(expected)
      or expected.status ~= 'active' or expected.revokedAt ~= JSON_NULL
      or expected.loggedOutAt ~= JSON_NULL
      or session.sessionHash ~= ARGV[6] or session.inviteId ~= ARGV[7]
      or session.ownerEmail ~= ARGV[8] or session.workspaceId ~= ARGV[9]
      or session.mailboxId ~= ARGV[10] or session.collaborationId ~= ARGV[11]
      or invite.inviteId ~= ARGV[7] or invite.ownerEmail ~= ARGV[8]
      or invite.workspaceId ~= ARGV[9] or invite.mailboxId ~= ARGV[10]
      or invite.collaborationId ~= ARGV[11] or invite.activeSessionHash ~= ARGV[6]
      or session.createdAt ~= invite.exchangedAt
      or integerValue(session.expiresAt) > integerValue(invite.expiresAt)
      then return cjson.encode({status='malformed'}) end
    if session.status ~= 'active' then return cjson.encode({status='revoked'}) end
    if invite.status == 'revoked' then return cjson.encode({status='revoked'}) end
    if invite.status ~= 'exchanged' then return cjson.encode({status='malformed'}) end
    if not sessionEqual(session, expected) then return cjson.encode({status='stale'}) end
    if integerValue(session.expiresAt) <= now or integerValue(invite.expiresAt) <= now then
      return cjson.encode({status='expired'})
    end
    if now < integerValue(session.createdAt) or now < integerValue(session.lastUsedAt) then
      return cjson.encode({status='malformed'})
    end
    if session.csrfTokenHash ~= ARGV[5] or session.lastUsedAt ~= ARGV[12] then
      return cjson.encode({status='stale'})
    end
    local csrfChanges = ARGV[2] ~= '' and ARGV[2] ~= session.csrfTokenHash
    if csrfChanges and ARGV[4] ~= '1' then return cjson.encode({status='malformed'}) end
    if ARGV[4] == '1' then
      if now <= integerValue(session.lastUsedAt) then return cjson.encode({status='malformed'}) end
      session.lastUsedAt = ARGV[1]
    elseif ARGV[4] ~= '0' then
      return cjson.encode({status='malformed'})
    end
    if ARGV[2] ~= '' then session.csrfTokenHash = ARGV[2] end
    if not sessionValid(session) or integerValue(ARGV[3]) ~= integerValue(session.expiresAt) - now then
      return cjson.encode({status='malformed'})
    end
    local sessionPttl = redis.call('PTTL', KEYS[1])
    local invitePttl = redis.call('PTTL', KEYS[2])
    local sessionAbsolutePttl = (integerValue(session.expiresAt) - now) * 1000
    local inviteAbsolutePttl = (integerValue(invite.expiresAt) - now) * 1000
    if sessionPttl <= 0 or invitePttl <= 0 or sessionAbsolutePttl <= 0 or inviteAbsolutePttl <= 0 then
      return cjson.encode({status='expired'})
    end
    local writePttl = math.min(sessionPttl, invitePttl, sessionAbsolutePttl, inviteAbsolutePttl)
    redis.call('SET', KEYS[1], cjson.encode(session), 'PX', math.floor(writePttl))
    return cjson.encode({status='updated', session=session})
    """.strip()


    def _update_v2_guest_session(
        session_record: dict,
        *,
        normalizer,
        now: int,
        csrf_token_hash: str | None = None,
        touch_last_used: bool = True,
        command_transport=None,
    ) -> dict:
        try:
            normalized_input = normalizer(session_record)
        except Exception:
            normalized_input = None
        if normalized_input is None:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        session_record = normalized_input
        session_wire = _v2_wire_json(session_record, "session")
        ttl = session_record.get("expiresAt", 0) - now if isinstance(session_record, dict) else 0
        session_hash = session_record.get("sessionHash") if isinstance(session_record, dict) else None
        expected_csrf_hash = session_record.get("csrfTokenHash") if isinstance(session_record, dict) else None
        session_key = build_v2_guest_session_key(session_hash)
        invite_key = build_v2_invite_key(session_record.get("inviteId")) if isinstance(session_record, dict) else None
        created_at = session_record.get("createdAt") if isinstance(session_record, dict) else None
        last_used_at = session_record.get("lastUsedAt") if isinstance(session_record, dict) else None
        expires_at = session_record.get("expiresAt") if isinstance(session_record, dict) else None
        csrf_hash_changes = (
            csrf_token_hash is not None and csrf_token_hash != expected_csrf_hash
        )
        advance_last_used = touch_last_used or csrf_hash_changes
        if (
            session_key is None
            or invite_key is None
            or session_wire is None
            or type(touch_last_used) is not bool
            or session_record.get("status") != "active"
            or session_record.get("revokedAt") is not None
            or session_record.get("loggedOutAt") is not None
            or not isinstance(expected_csrf_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_csrf_hash)
            or (csrf_token_hash is not None and (not isinstance(csrf_token_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", csrf_token_hash)))
            or type(now) is not int or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or type(created_at) is not int or not MIN_V2_TIMESTAMP_SECONDS <= created_at <= MAX_V2_TIMESTAMP_SECONDS
            or type(last_used_at) is not int or not MIN_V2_TIMESTAMP_SECONDS <= last_used_at <= MAX_V2_TIMESTAMP_SECONDS
            or type(expires_at) is not int or not MIN_V2_TIMESTAMP_SECONDS <= expires_at <= MAX_V2_TIMESTAMP_SECONDS
            or expires_at - created_at > 28_800
            or now < created_at
            or now < last_used_at
            or (advance_last_used and now <= last_used_at)
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        if ttl <= 0:
            return {"status": "expired", "error": {"code": "session_expired"}}
        if not advance_last_used:
            return {"status": "unchanged"}
        result = _v2_eval(
            [
                "EVAL", _UPDATE_V2_SESSION_LUA, 2, session_key, invite_key,
                str(now), csrf_token_hash or "", str(ttl), "1" if advance_last_used else "0",
                expected_csrf_hash, session_hash, session_record.get("inviteId"),
                session_record.get("ownerEmail"), session_record.get("workspaceId"),
                session_record.get("mailboxId"), session_record.get("collaborationId"),
                str(session_record.get("lastUsedAt")),
                session_wire,
            ],
            command_transport,
            response_shapes={
                "updated": {"session"}, "missing": set(), "expired": set(),
                "revoked": set(), "stale": set(), "malformed": set(),
            },
        )
        if result.get("status") == "updated":
            expected_session = dict(session_record)
            if advance_last_used:
                expected_session["lastUsedAt"] = now
            if csrf_token_hash is not None:
                expected_session["csrfTokenHash"] = csrf_token_hash
            try:
                normalized_result = normalizer(
                    _v2_record_from_wire(result.get("session"), "session")
                )
            except Exception:
                normalized_result = None
            if normalized_result != expected_session:
                return {"status": "unavailable", "error": {"code": "storage_protocol_error"}}
            return _V2RecordResult(expected_session, status="updated")
        return result


    _REVOKE_V2_INVITE_LUA = _V2_LUA_COMMON + r"""
    local inviteState, raw = readString(KEYS[1], 16384)
    if inviteState == 'missing' then return cjson.encode({status='missing'}) end
    if inviteState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local inviteOk, invite = decodeWire(raw)
    if not inviteOk or not inviteValid(invite) then return cjson.encode({status='malformed'}) end
    if invite.ownerEmail ~= ARGV[1] or invite.workspaceId ~= ARGV[2]
      or invite.mailboxId ~= ARGV[3] or invite.collaborationId ~= ARGV[4]
      or invite.inviteId ~= ARGV[5] or ARGV[6] ~= ARGV[1]
      or ARGV[7] ~= 'revoke_invite' then return cjson.encode({status='forbidden'}) end
    if invite.activeSessionHash ~= nil and #KEYS < 2 then
      return cjson.encode({status='retry'})
    end
    if invite.activeSessionHash == nil and #KEYS > 1 then return cjson.encode({status='malformed'}) end
    if not timestampSeconds(ARGV[8]) then return cjson.encode({status='malformed'}) end
    local now = integerValue(ARGV[8])
    local inviteAlreadyRevoked = invite.status == 'revoked'
    if inviteAlreadyRevoked then
      if now < integerValue(invite.createdAt)
        or (invite.exchangedAt ~= JSON_NULL and now < integerValue(invite.exchangedAt))
        or (invite.revokedAt ~= JSON_NULL and now < integerValue(invite.revokedAt)) then
        return cjson.encode({status='malformed'})
      end
    elseif now <= integerValue(invite.createdAt)
      or (invite.exchangedAt ~= JSON_NULL and now <= integerValue(invite.exchangedAt)) then
      return cjson.encode({status='malformed'})
    end
    local invitePttl = redis.call('PTTL', KEYS[1])
    local inviteAbsolutePttl = (integerValue(invite.expiresAt) - now) * 1000
    if invitePttl <= 0 or inviteAbsolutePttl <= 0 then return cjson.encode({status='expired'}) end
    local session = nil
    local sessionTtl = nil
    local writeSession = false
    if #KEYS > 1 then
      local sessionState, sessionRaw = readString(KEYS[2], 16384)
      if sessionState ~= 'ok' then return cjson.encode({status='malformed'}) end
      local sessionOk
      sessionOk, session = decodeWire(sessionRaw)
      if not sessionOk or not sessionValid(session)
        or session.sessionHash ~= invite.activeSessionHash or session.inviteId ~= invite.inviteId
        or session.ownerEmail ~= invite.ownerEmail or session.workspaceId ~= invite.workspaceId
        or session.mailboxId ~= invite.mailboxId or session.collaborationId ~= invite.collaborationId
        or session.createdAt ~= invite.exchangedAt
        or session.allowedActions[1] ~= 'read' or session.allowedActions[2] ~= 'reply'
        or session.visibility ~= 'shared_only'
        or integerValue(session.expiresAt) > integerValue(invite.expiresAt) then
        return cjson.encode({status='malformed'})
      end
      if inviteAlreadyRevoked then
        if now < integerValue(session.createdAt) or now < integerValue(session.lastUsedAt)
          or (session.revokedAt ~= JSON_NULL and now < integerValue(session.revokedAt))
          or (session.loggedOutAt ~= JSON_NULL and now < integerValue(session.loggedOutAt)) then
          return cjson.encode({status='malformed'})
        end
      elseif now <= integerValue(session.lastUsedAt)
        or (session.revokedAt ~= JSON_NULL and now <= integerValue(session.revokedAt))
        or (session.loggedOutAt ~= JSON_NULL and now <= integerValue(session.loggedOutAt)) then
        return cjson.encode({status='malformed'})
      end
      if integerValue(session.expiresAt) <= now then return cjson.encode({status='expired'}) end
      local currentSessionPttl = redis.call('PTTL', KEYS[2])
      local absoluteSessionPttl = (integerValue(session.expiresAt) - now) * 1000
      if currentSessionPttl <= 0 or absoluteSessionPttl <= 0 then return cjson.encode({status='expired'}) end
      sessionTtl = math.min(currentSessionPttl, absoluteSessionPttl, invitePttl, inviteAbsolutePttl)
    end
    if invite.status == 'revoked' then return cjson.encode({status='already_revoked'}) end
    if invite.status == 'expired' or integerValue(invite.expiresAt) <= now then return cjson.encode({status='expired'}) end
    if session then
      if session.status == 'active' then
        session.status = 'revoked'
        session.revokedAt = ARGV[8]
        if not sessionValid(session) then return cjson.encode({status='malformed'}) end
        writeSession = true
      elseif session.status == 'logged_out' or session.status == 'revoked' then
        writeSession = false
      elseif session.status == 'expired' then
        return cjson.encode({status='expired'})
      else
        return cjson.encode({status='malformed'})
      end
    end
    invite.status = 'revoked'
    invite.revokedAt = ARGV[8]
    invite.revokedBy = ARGV[6]
    if not inviteValid(invite) then return cjson.encode({status='malformed'}) end
    local inviteWritePttl = math.min(invitePttl, inviteAbsolutePttl)
    redis.call('SET', KEYS[1], cjson.encode(invite), 'PX', math.floor(inviteWritePttl))
    if writeSession then
      redis.call('SET', KEYS[2], cjson.encode(session), 'PX', math.floor(sessionTtl))
    end
    return cjson.encode({status='revoked_ok'})
    """.strip()


    def _revoke_v2_invite(
        invite_id: str,
        *,
        owner_email: str,
        workspace_id: str,
        mailbox_id: str,
        collaboration_id: str,
        revoked_by: str,
        now: int,
        command_transport=None,
    ) -> dict:
        if (
            type(now) is not int
            or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or normalize_v2_email(owner_email) != owner_email
            or normalize_v2_workspace_id(workspace_id) != workspace_id
            or revoked_by != owner_email
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        invite_key = build_v2_invite_key(invite_id)
        if invite_key is None:
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        loaded = _v2_read_json(invite_key, "invite", command_transport)
        if loaded.get("status") == "missing":
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        invite = normalize_v2_invite_record(loaded.get("record")) if loaded.get("status") == "ok" else None
        if invite is None or invite.get("inviteId") != invite_id:
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        prior_audits = [invite["createdAt"]]
        prior_audits.extend(
            timestamp
            for timestamp in (invite.get("exchangedAt"), invite.get("revokedAt"))
            if timestamp is not None
        )
        chronology_invalid = (
            any(now < timestamp for timestamp in prior_audits)
            if invite.get("status") == "revoked"
            else any(now <= timestamp for timestamp in prior_audits)
        )
        if chronology_invalid:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        keys = [invite_key]
        active_hash = invite.get("activeSessionHash")
        if active_hash:
            session_key = build_v2_guest_session_key(active_hash)
            if session_key is None:
                return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
            keys.append(session_key)
        result = _v2_eval(
            ["EVAL", _REVOKE_V2_INVITE_LUA, len(keys), *keys,
             owner_email, workspace_id, mailbox_id, collaboration_id, invite_id,
             revoked_by, "revoke_invite", str(now)],
            command_transport,
            response_shapes={
                "revoked_ok": set(), "already_revoked": set(), "retry": set(),
                "missing": set(), "forbidden": set(), "malformed": set(), "expired": set(),
            },
        )
        if result.get("status") == "forbidden":
            return {"status": "forbidden", "error": {"code": "forbidden"}}
        if result.get("status") == "revoked_ok":
            return {"status": "ok"}
        if result.get("status") == "already_revoked":
            return {"status": "already_revoked", "error": {"code": "already_revoked"}}
        if result.get("status") == "retry":
            return {"status": "conflict", "error": {"code": "stale_invitation"}}
        if result.get("status") == "missing":
            return {"status": "missing", "error": {"code": "invite_not_found"}}
        if result.get("status") == "malformed":
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        if result.get("status") == "expired":
            return {"status": "expired", "error": {"code": "invite_expired"}}
        return result


    _REVOKE_V2_SESSION_LUA = _V2_LUA_COMMON + r"""
    local sessionState, raw = readString(KEYS[1], 16384)
    if sessionState == 'missing' then return cjson.encode({status='missing'}) end
    if sessionState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local sessionOk, session = decodeWire(raw)
    local inviteState, inviteRaw = readString(KEYS[2], 16384)
    if inviteState == 'missing' then return cjson.encode({status='invite_missing'}) end
    if inviteState ~= 'ok' then return cjson.encode({status='malformed'}) end
    local inviteOk, invite = decodeWire(inviteRaw)
    if not sessionOk or not inviteOk or not sessionValid(session) or not inviteValid(invite)
      or session.sessionHash ~= ARGV[2] or session.inviteId ~= ARGV[3]
      or session.ownerEmail ~= ARGV[4] or session.workspaceId ~= ARGV[5]
      or session.mailboxId ~= ARGV[6] or session.collaborationId ~= ARGV[7]
      or invite.inviteId ~= ARGV[3] or invite.ownerEmail ~= ARGV[4]
      or invite.workspaceId ~= ARGV[5] or invite.mailboxId ~= ARGV[6]
      or invite.collaborationId ~= ARGV[7] or invite.activeSessionHash ~= ARGV[2]
      or session.createdAt ~= invite.exchangedAt
      or integerValue(session.expiresAt) > integerValue(invite.expiresAt) then
      return cjson.encode({status='malformed'})
    end
    if not timestampSeconds(ARGV[1]) then return cjson.encode({status='malformed'}) end
    local now = integerValue(ARGV[1])
    if now < integerValue(session.createdAt) or now < integerValue(session.lastUsedAt)
      or (session.revokedAt ~= JSON_NULL and now < integerValue(session.revokedAt))
      or (session.loggedOutAt ~= JSON_NULL and now < integerValue(session.loggedOutAt))
      or (invite.exchangedAt ~= JSON_NULL and now < integerValue(invite.exchangedAt))
      or (invite.revokedAt ~= JSON_NULL and now < integerValue(invite.revokedAt)) then
      return cjson.encode({status='malformed'})
    end
    if session.status == 'revoked' or session.status == 'logged_out' or session.loggedOutAt ~= cjson.null then
      return cjson.encode({status='already_logged_out'})
    end
    if invite.status == 'revoked' then return cjson.encode({status='already_logged_out'}) end
    if invite.status ~= 'exchanged' then return cjson.encode({status='malformed'}) end
    if session.status == 'expired' then return cjson.encode({status='expired'}) end
    if session.status ~= 'active' then return cjson.encode({status='malformed'}) end
    if now <= integerValue(session.lastUsedAt) then return cjson.encode({status='malformed'}) end
    if now >= integerValue(session.expiresAt) or now >= integerValue(invite.expiresAt) then
      return cjson.encode({status='expired'})
    end
    session.status = 'logged_out'
    session.loggedOutAt = ARGV[1]
    if not sessionValid(session) then return cjson.encode({status='malformed'}) end
    local currentPttl = redis.call('PTTL', KEYS[1])
    local invitePttl = redis.call('PTTL', KEYS[2])
    local absolutePttl = (integerValue(session.expiresAt) - now) * 1000
    local inviteAbsolutePttl = (integerValue(invite.expiresAt) - now) * 1000
    if currentPttl <= 0 or invitePttl <= 0 or absolutePttl <= 0 or inviteAbsolutePttl <= 0 then
      return cjson.encode({status='expired'})
    end
    local ttl = math.min(currentPttl, invitePttl, absolutePttl, inviteAbsolutePttl)
    redis.call('SET', KEYS[1], cjson.encode(session), 'PX', math.floor(ttl))
    return cjson.encode({status='revoked_ok'})
    """.strip()


    def _revoke_v2_guest_session(
        session_hash: str,
        *,
        invite_id: str,
        owner_email: str,
        workspace_id: str,
        mailbox_id: str,
        collaboration_id: str,
        now: int,
        command_transport=None,
    ) -> dict:
        normalized_owner = normalize_v2_email(owner_email)
        if (
            type(now) is not int
            or not MIN_V2_TIMESTAMP_SECONDS <= now <= MAX_V2_TIMESTAMP_SECONDS
            or normalized_owner is None
            or owner_email != normalized_owner
            or normalize_v2_workspace_id(workspace_id) != workspace_id
            or not isinstance(mailbox_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,255}", mailbox_id)
            or build_v2_thread_key(collaboration_id) is None
        ):
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        session_key = build_v2_guest_session_key(session_hash)
        invite_key = build_v2_invite_key(invite_id)
        if session_key is None or invite_key is None:
            return {"status": "malformed", "error": {"code": "invalid_request"}}
        result = _v2_eval(
            [
                "EVAL", _REVOKE_V2_SESSION_LUA, 2, session_key, invite_key,
                str(now), session_hash, invite_id, normalized_owner,
                workspace_id, mailbox_id, collaboration_id,
            ],
            command_transport,
            response_shapes={
                "revoked_ok": set(), "already_logged_out": set(), "missing": set(),
                "invite_missing": set(), "malformed": set(), "expired": set(),
            },
        )
        if result.get("status") == "revoked_ok":
            return {"status": "ok"}
        if result.get("status") == "missing":
            return {"status": "missing", "error": {"code": "session_not_found"}}
        if result.get("status") == "invite_missing":
            return {"status": "revoked", "error": {"code": "session_revoked"}}
        if result.get("status") == "already_logged_out":
            return {"status": "already_logged_out", "error": {"code": "already_logged_out"}}
        if result.get("status") == "malformed":
            return {"status": "malformed", "error": {"code": "storage_protocol_error"}}
        if result.get("status") == "expired":
            return {"status": "expired", "error": {"code": "session_expired"}}
        return result
