from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.user_config_store"
_LEGACY_MODULE_NAME = "user_config_store"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "User-config helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy user-config identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import json
    import os
    import re
    import secrets
    from collections.abc import Mapping
    from copy import deepcopy
    from datetime import datetime, timezone
    from typing import Literal, TypedDict
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    from .auth import http as auth_http
    from .auth import runtime as auth_runtime
    from .auth.email_address import is_valid_auth_email, normalize_auth_email
    from .inboxes.mailbox_secret_store import is_valid_mailbox_credential_version

    USER_CONFIG_SCHEMA_VERSION = 1
    USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"
    MAX_USER_CONFIG_STORE_RESPONSE_BYTES = 256 * 1024
    ONBOARDING_PRESET_INBOX_IDS = frozenset(
        {
            "main",
            "demo",
            "business",
            "promo",
            "legal",
            "finance",
            "royalty",
            "sync",
        }
    )
    ONBOARDING_CUSTOM_INBOX_ID_PATTERN = re.compile(
        r"^custom:[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    MAX_ONBOARDING_INBOX_ID_LENGTH = 160
    MAILBOX_CREDENTIAL_VERSION_FIELD = "credentialVersion"
    MAILBOX_MUTATION_LEASE_KEY_PREFIX = "cuevion:mailbox-mutation-lease:v1"
    # One lease can cover 16 two-namespace probes (2 x 20s each), a 30s IMAP
    # attempt, four 20s-read/20s-write config CAS attempts, exact readbacks,
    # and the bounded config/secret compensation paths. Twenty-five minutes
    # keeps that worst-case failure envelope covered while still expiring a
    # crashed worker without operator intervention.
    MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS = 25 * 60 * 1000
    MAX_CUSTOM_IMAP_CONFIG_WRITE_ATTEMPTS = 4
    MAILBOX_MUTATION_LEASE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


    class AuthenticatedUserContext(TypedDict):
        email: str
        name: str
        userType: str


    class UserConfigStoreContext(TypedDict):
        rest_url: str
        rest_token: str


    UserConfigAccessErrorCode = Literal[
        "session_auth_unavailable",
        "missing_session",
        "invalid_session",
        "user_config_store_unavailable",
        "user_config_not_found",
        "user_config_malformed",
        "invalid_mailbox_id",
        "managed_inbox_not_found",
        "duplicate_mailbox_id",
        "managed_inbox_malformed",
        "mailbox_id_conflict",
        "managed_inbox_provider_mismatch",
        "onboarding_unavailable",
        "onboarding_completed",
        "onboarding_malformed",
        "unknown_inbox_position",
        "inbox_position_not_selected",
        "inbox_position_conflict",
        "mailbox_email_conflict",
        "onboarding_incomplete",
        "user_config_write_conflict",
        "user_config_newer_mailbox_preserved",
        "user_config_rollback_ambiguous",
        "mailbox_mutation_lease_conflict",
        "mailbox_mutation_lease_ambiguous",
    ]


    class UserConfigAccessError(TypedDict):
        code: UserConfigAccessErrorCode
        message: str


    class UserConfigReadResult(TypedDict):
        status: Literal["ok", "missing", "unavailable", "malformed", "unauthorized"]
        config: dict | None
        error: UserConfigAccessError | None


    class UserConfigWriteResult(TypedDict):
        status: Literal["ok", "unavailable"]
        record: dict | None
        error: UserConfigAccessError | None


    class MailboxMutationLeaseResult(TypedDict):
        status: Literal[
            "acquired",
            "held",
            "released",
            "not_owned",
            "ambiguous",
            "malformed",
            "unavailable",
        ]
        token: str | None
        error: UserConfigAccessError | None


    class OwnedManagedInboxContext(TypedDict):
        id: str
        email: str
        provider: str | None
        connected: bool
        connectionStatus: str


    class OwnedManagedInboxResult(TypedDict):
        status: Literal["ok", "unauthorized", "unavailable", "not_found", "malformed"]
        inbox: OwnedManagedInboxContext | None
        error: UserConfigAccessError | None


    class OwnedManagedInboxRecordResult(TypedDict):
        status: Literal[
            "ok",
            "unauthorized",
            "unavailable",
            "not_found",
            "malformed",
            "conflict",
        ]
        user: AuthenticatedUserContext | None
        inbox: dict | None
        config: dict | None
        error: UserConfigAccessError | None


    def _error(code: UserConfigAccessErrorCode, message: str) -> UserConfigAccessError:
        return {"code": code, "message": message}


    def resolve_authenticated_user(
        headers,
    ) -> tuple[AuthenticatedUserContext | None, UserConfigAccessError | None]:
        try:
            if type(headers) in (list, tuple):
                raw_headers = tuple(headers)
            elif callable(getattr(headers, "raw_items", None)):
                raw_headers = tuple(headers.raw_items())
            elif isinstance(headers, Mapping):
                raw_headers = tuple(headers.items())
            else:
                raw_headers = ()
            resolution = auth_runtime.resolve_authenticated_member(raw_headers)
        except auth_http.HttpBoundaryError:
            return None, _error(
                "invalid_session",
                "The authenticated session is invalid.",
            )
        except Exception:
            return None, _error(
                "session_auth_unavailable",
                "Authenticated session validation is unavailable.",
            )

        if resolution.outcome is auth_runtime.MemberResolutionOutcome.UNAVAILABLE:
            return None, _error(
                "session_auth_unavailable",
                "Authenticated session validation is unavailable.",
            )

        if resolution.outcome is auth_runtime.MemberResolutionOutcome.UNAUTHENTICATED:
            error_code: UserConfigAccessErrorCode = (
                "invalid_session" if resolution.set_cookies else "missing_session"
            )
            message = (
                "The authenticated session is invalid."
                if error_code == "invalid_session"
                else "An authenticated session is required."
            )
            return None, _error(error_code, message)

        member = resolution.member
        if member is None:
            return None, _error(
                "session_auth_unavailable",
                "Authenticated session validation is unavailable.",
            )

        if member.auth_source != "auth0" or member.user_type != "member":
            return None, _error("missing_session", "An authenticated session is required.")

        return {
            "email": member.email,
            "name": member.name,
            "userType": member.user_type,
        }, None


    def resolve_user_config_store(
    ) -> tuple[UserConfigStoreContext | None, UserConfigAccessError | None]:
        rest_url = os.getenv("KV_REST_API_URL", "").strip()
        rest_token = os.getenv("KV_REST_API_TOKEN", "").strip()

        if not rest_url or not rest_token:
            return None, _error(
                "user_config_store_unavailable",
                "User config storage is not configured.",
            )

        return {
            "rest_url": rest_url.rstrip("/"),
            "rest_token": rest_token,
        }, None


    def build_user_config_key(owner_email: str) -> str:
        return f"{USER_CONFIG_KEY_PREFIX}:{normalize_auth_email(owner_email)}"


    def build_mailbox_mutation_lease_key(
        owner_email: str,
        mailbox_id: str,
    ) -> str:
        return (
            f"{MAILBOX_MUTATION_LEASE_KEY_PREFIX}:"
            f"{normalize_auth_email(owner_email)}:{mailbox_id.casefold()}"
        )


    def _strip_known_mailbox_passwords(config: dict) -> dict:
        sanitized = deepcopy(config)

        def strip_connection(connection):
            if not isinstance(connection, dict):
                return
            for settings_name in ("customImap", "customSmtp"):
                settings = connection.get(settings_name)
                if isinstance(settings, dict):
                    settings.pop("password", None)

        managed_inboxes = sanitized.get("managedInboxes")
        if isinstance(managed_inboxes, list):
            for inbox in managed_inboxes:
                strip_connection(inbox)

        onboarding_session = sanitized.get("onboardingSession")
        if isinstance(onboarding_session, dict):
            state = onboarding_session.get("state")
            if isinstance(state, dict):
                connections = state.get("inboxConnections")
                if isinstance(connections, dict):
                    for connection in connections.values():
                        strip_connection(connection)

        return sanitized


    def _json_values_are_type_exact(left, right) -> bool:
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


    def _contains_mailbox_credential_generation(value: object) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    compact = "".join(
                        character
                        for character in key.strip().lower()
                        if character.isalnum()
                    )
                    if (
                        ("credential" in compact or "secret" in compact)
                        and (
                            "version" in compact
                            or "generation" in compact
                            or "revision" in compact
                        )
                    ):
                        return True
                if _contains_mailbox_credential_generation(item):
                    return True
        elif isinstance(value, list):
            return any(_contains_mailbox_credential_generation(item) for item in value)
        return False


    def _perform_rest_request(
        store: UserConfigStoreContext,
        method: str,
        path: str,
        body: bytes | None = None,
    ) -> tuple[dict | None, UserConfigAccessError | None]:
        request = Request(
            f"{store['rest_url']}{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {store['rest_token']}",
                "Content-Type": "application/json",
            },
            method=method,
        )

        try:
            with urlopen(request, timeout=20) as response:
                raw_payload = response.read(MAX_USER_CONFIG_STORE_RESPONSE_BYTES + 1)
                if len(raw_payload) > MAX_USER_CONFIG_STORE_RESPONSE_BYTES:
                    return None, _error(
                        "user_config_store_unavailable",
                        "User config storage returned an invalid response.",
                    )
                if not raw_payload:
                    return None, _error(
                        "user_config_store_unavailable",
                        "User config storage returned an invalid response.",
                    )
                payload = json.loads(raw_payload.decode("utf-8"))
                if not isinstance(payload, dict):
                    return None, _error(
                        "user_config_store_unavailable",
                        "User config storage returned an invalid response.",
                    )
                return payload, None
        except HTTPError:
            return None, _error(
                "user_config_store_unavailable",
                "User config storage is temporarily unavailable.",
            )
        except (TimeoutError, URLError, OSError):
            return None, _error(
                "user_config_store_unavailable",
                "User config storage is temporarily unavailable.",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, _error(
                "user_config_store_unavailable",
                "User config storage returned an invalid response.",
            )
        except Exception:
            return None, _error(
                "user_config_store_unavailable",
                "User config storage is temporarily unavailable.",
            )


    def read_user_config_record(
        store: UserConfigStoreContext,
        owner_email: str,
    ) -> UserConfigReadResult:
        payload, error = _perform_rest_request(
            store,
            "GET",
            f"/get/{quote(build_user_config_key(owner_email), safe='')}",
        )
        if error:
            return {"status": "unavailable", "config": None, "error": error}

        if not isinstance(payload, dict):
            return {
                "status": "malformed",
                "config": None,
                "error": _error(
                    "user_config_malformed",
                    "User config storage returned an unreadable response.",
                ),
            }

        if "result" not in payload:
            return {
                "status": "unavailable",
                "config": None,
                "error": _error(
                    "user_config_store_unavailable",
                    "User config storage returned an invalid response.",
                ),
            }
        result = payload.get("result")
        if result is None:
            return {
                "status": "missing",
                "config": None,
                "error": _error("user_config_not_found", "User config was not found."),
            }

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return {
                    "status": "malformed",
                    "config": None,
                    "error": _error(
                        "user_config_malformed",
                        "User config storage returned malformed JSON.",
                    ),
                }

        if not isinstance(result, dict):
            return {
                "status": "malformed",
                "config": None,
                "error": _error("user_config_malformed", "User config record is malformed."),
            }

        return {"status": "ok", "config": result, "error": None}


    def write_user_config_record(
        store: UserConfigStoreContext,
        owner_email: str,
        record: dict,
    ) -> UserConfigWriteResult:
        encoded_record = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload, error = _perform_rest_request(
            store,
            "POST",
            f"/set/{quote(build_user_config_key(owner_email), safe='')}",
            body=encoded_record,
        )
        if error:
            return {"status": "unavailable", "record": None, "error": error}

        if not isinstance(payload, dict) or payload.get("result") != "OK":
            return {
                "status": "unavailable",
                "record": None,
                "error": _error(
                    "user_config_store_unavailable",
                    "User config storage did not confirm the write.",
                ),
            }

        return {
            "status": "ok",
            "record": payload,
            "error": None,
        }


    _WRITE_USER_CONFIG_IF_UNCHANGED_LUA = r"""
    local current = redis.call('GET', KEYS[1])
    if not current then return 'missing' end
    if current ~= ARGV[1] then return 'stale' end
    redis.call('SET', KEYS[1], ARGV[2])
    return 'saved'
    """.strip()

    _WRITE_USER_CONFIG_IF_MISSING_LUA = r"""
    local current = redis.call('GET', KEYS[1])
    if current then return 'exists' end
    redis.call('SET', KEYS[1], ARGV[1])
    return 'saved'
    """.strip()

    _ACQUIRE_MAILBOX_MUTATION_LEASE_LUA = r"""
    local result = redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2])
    if result then return 'acquired' end
    return 'held'
    """.strip()

    _RELEASE_MAILBOX_MUTATION_LEASE_LUA = r"""
    local current = redis.call('GET', KEYS[1])
    if not current then return 'missing' end
    if current ~= ARGV[1] then return 'not_owner' end
    redis.call('DEL', KEYS[1])
    return 'released'
    """.strip()


    def _mailbox_mutation_lease_result(
        status: Literal[
            "acquired",
            "held",
            "released",
            "not_owned",
            "ambiguous",
            "malformed",
            "unavailable",
        ],
        *,
        token: str | None = None,
        error: UserConfigAccessError | None = None,
    ) -> MailboxMutationLeaseResult:
        return {"status": status, "token": token, "error": error}


    def _mailbox_mutation_lease_identity_is_valid(
        owner_email: object,
        mailbox_id: object,
    ) -> bool:
        if (
            not isinstance(owner_email, str)
            or not isinstance(mailbox_id, str)
            or not mailbox_id
            or mailbox_id != mailbox_id.strip()
            or len(mailbox_id) > 192
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", mailbox_id)
        ):
            return False
        normalized_owner = normalize_auth_email(owner_email)
        return bool(normalized_owner) and is_valid_auth_email(normalized_owner)


    def _read_mailbox_mutation_lease(
        store: UserConfigStoreContext,
        owner_email: str,
        mailbox_id: str,
    ) -> tuple[Literal["present", "missing", "unavailable", "malformed"], str | None]:
        payload, error = _perform_rest_request(
            store,
            "GET",
            (
                "/get/"
                + quote(
                    build_mailbox_mutation_lease_key(owner_email, mailbox_id),
                    safe="",
                )
            ),
        )
        if error:
            return "unavailable", None
        if not isinstance(payload, dict) or set(payload) != {"result"}:
            return "malformed", None
        result = payload.get("result")
        if result is None:
            return "missing", None
        if (
            not isinstance(result, str)
            or not MAILBOX_MUTATION_LEASE_TOKEN_PATTERN.fullmatch(result)
        ):
            return "malformed", None
        return "present", result


    def _perform_mailbox_mutation_lease_command(
        store: UserConfigStoreContext,
        command: list,
    ) -> tuple[str | None, UserConfigAccessError | None]:
        payload, error = _perform_rest_request(
            store,
            "POST",
            "",
            body=json.dumps(command, separators=(",", ":")).encode("utf-8"),
        )
        if error:
            return None, error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"result"}
            or not isinstance(payload.get("result"), str)
        ):
            return None, _error(
                "mailbox_mutation_lease_ambiguous",
                "Mailbox mutation lease acknowledgement is invalid.",
            )
        return payload["result"], None


    def acquire_mailbox_mutation_lease(
        owner_email: str,
        mailbox_id: str,
    ) -> MailboxMutationLeaseResult:
        """Atomically reserve one authoritative mailbox for the complete write saga."""
        if not _mailbox_mutation_lease_identity_is_valid(owner_email, mailbox_id):
            return _mailbox_mutation_lease_result(
                "malformed",
                error=_error(
                    "user_config_malformed",
                    "Mailbox mutation lease identity is invalid.",
                ),
            )
        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return _mailbox_mutation_lease_result(
                "unavailable",
                error=store_error
                or _error(
                    "user_config_store_unavailable",
                    "Mailbox mutation lease storage is unavailable.",
                ),
            )

        token = secrets.token_urlsafe(32)
        if not MAILBOX_MUTATION_LEASE_TOKEN_PATTERN.fullmatch(token):
            return _mailbox_mutation_lease_result(
                "unavailable",
                error=_error(
                    "user_config_store_unavailable",
                    "Mailbox mutation lease token could not be generated.",
                ),
            )
        result, command_error = _perform_mailbox_mutation_lease_command(
            store,
            [
                "EVAL",
                _ACQUIRE_MAILBOX_MUTATION_LEASE_LUA,
                1,
                build_mailbox_mutation_lease_key(owner_email, mailbox_id),
                token,
                str(MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS),
            ],
        )
        if result == "acquired":
            return _mailbox_mutation_lease_result("acquired", token=token)
        if result == "held":
            return _mailbox_mutation_lease_result(
                "held",
                error=_error(
                    "mailbox_mutation_lease_conflict",
                    "Another mailbox mutation is already in progress.",
                ),
            )

        read_status, current_token = _read_mailbox_mutation_lease(
            store,
            owner_email,
            mailbox_id,
        )
        if read_status == "present" and current_token == token:
            return _mailbox_mutation_lease_result("acquired", token=token)
        if read_status == "present":
            return _mailbox_mutation_lease_result(
                "held",
                error=_error(
                    "mailbox_mutation_lease_conflict",
                    "Another mailbox mutation is already in progress.",
                ),
            )
        return _mailbox_mutation_lease_result(
            "ambiguous",
            error=command_error
            or _error(
                "mailbox_mutation_lease_ambiguous",
                "Mailbox mutation lease acquisition could not be verified.",
            ),
        )


    def release_mailbox_mutation_lease(
        owner_email: str,
        mailbox_id: str,
        token: str,
    ) -> MailboxMutationLeaseResult:
        """Release only the caller's exact lease, classifying a lost ACK by readback."""
        if (
            not _mailbox_mutation_lease_identity_is_valid(owner_email, mailbox_id)
            or not isinstance(token, str)
            or not MAILBOX_MUTATION_LEASE_TOKEN_PATTERN.fullmatch(token)
        ):
            return _mailbox_mutation_lease_result(
                "malformed",
                error=_error(
                    "user_config_malformed",
                    "Mailbox mutation lease release state is invalid.",
                ),
            )
        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return _mailbox_mutation_lease_result(
                "unavailable",
                token=token,
                error=store_error
                or _error(
                    "user_config_store_unavailable",
                    "Mailbox mutation lease storage is unavailable.",
                ),
            )

        result, command_error = _perform_mailbox_mutation_lease_command(
            store,
            [
                "EVAL",
                _RELEASE_MAILBOX_MUTATION_LEASE_LUA,
                1,
                build_mailbox_mutation_lease_key(owner_email, mailbox_id),
                token,
            ],
        )
        if result in {"released", "missing"}:
            return _mailbox_mutation_lease_result("released", token=token)
        if result == "not_owner":
            return _mailbox_mutation_lease_result("not_owned", token=token)

        read_status, current_token = _read_mailbox_mutation_lease(
            store,
            owner_email,
            mailbox_id,
        )
        if read_status == "missing":
            return _mailbox_mutation_lease_result("released", token=token)
        if read_status == "present" and current_token != token:
            return _mailbox_mutation_lease_result("not_owned", token=token)
        return _mailbox_mutation_lease_result(
            "ambiguous",
            token=token,
            error=command_error
            or _error(
                "mailbox_mutation_lease_ambiguous",
                "Mailbox mutation lease release could not be verified.",
            ),
        )


    def write_user_config_record_if_missing(
        store: UserConfigStoreContext,
        owner_email: str,
        replacement_record: dict,
    ) -> dict:
        """Atomically create one canonical config record only while it is absent."""
        if not isinstance(replacement_record, dict):
            return {
                "status": "malformed",
                "record": None,
                "error": _error(
                    "user_config_malformed",
                    "User config storage received an invalid conditional write.",
                ),
            }

        safe_replacement = _strip_known_mailbox_passwords(replacement_record)
        replacement_wire = json.dumps(
            safe_replacement,
            separators=(",", ":"),
            sort_keys=True,
        )
        command = [
            "EVAL",
            _WRITE_USER_CONFIG_IF_MISSING_LUA,
            1,
            build_user_config_key(owner_email),
            replacement_wire,
        ]
        payload, error = _perform_rest_request(
            store,
            "POST",
            "",
            body=json.dumps(command, separators=(",", ":")).encode("utf-8"),
        )
        if error:
            return {"status": "unavailable", "record": None, "error": error}
        if not isinstance(payload, dict) or set(payload) != {"result"}:
            return {
                "status": "unavailable",
                "record": None,
                "error": _error(
                    "user_config_store_unavailable",
                    "User config storage returned an invalid conditional write response.",
                ),
            }
        if payload["result"] == "saved":
            return {"status": "ok", "record": safe_replacement, "error": None}
        if payload["result"] == "exists":
            return {
                "status": "conflict",
                "record": None,
                "error": _error(
                    "user_config_write_conflict",
                    "User config changed before it could be created.",
                ),
            }
        return {
            "status": "unavailable",
            "record": None,
            "error": _error(
                "user_config_store_unavailable",
                "User config storage returned an invalid conditional write response.",
            ),
        }


    def write_user_config_record_if_unchanged(
        store: UserConfigStoreContext,
        owner_email: str,
        expected_record: dict,
        replacement_record: dict,
    ) -> dict:
        """Atomically replace one canonical config record when it is unchanged."""
        if not isinstance(expected_record, dict) or not isinstance(
            replacement_record, dict
        ):
            return {
                "status": "malformed",
                "record": None,
                "error": _error(
                    "user_config_malformed",
                    "User config storage received an invalid conditional write.",
                ),
            }

        safe_replacement = _strip_known_mailbox_passwords(replacement_record)
        # Compare the exact server-read record so this CAS can also scrub legacy
        # password fields instead of becoming permanently stale on their presence.
        expected_wire = json.dumps(
            expected_record,
            separators=(",", ":"),
            sort_keys=True,
        )
        replacement_wire = json.dumps(
            safe_replacement,
            separators=(",", ":"),
            sort_keys=True,
        )
        command = [
            "EVAL",
            _WRITE_USER_CONFIG_IF_UNCHANGED_LUA,
            1,
            build_user_config_key(owner_email),
            expected_wire,
            replacement_wire,
        ]
        payload, error = _perform_rest_request(
            store,
            "POST",
            "",
            body=json.dumps(command, separators=(",", ":")).encode("utf-8"),
        )
        if error:
            return {"status": "unavailable", "record": None, "error": error}
        if not isinstance(payload, dict) or set(payload) != {"result"}:
            return {
                "status": "unavailable",
                "record": None,
                "error": _error(
                    "user_config_store_unavailable",
                    "User config storage returned an invalid conditional write response.",
                ),
            }

        result = payload.get("result")
        if result == "saved":
            return {"status": "ok", "record": safe_replacement, "error": None}
        if result == "stale":
            return {
                "status": "conflict",
                "record": None,
                "error": _error(
                    "user_config_write_conflict",
                    "User config changed before the mailbox update could be committed.",
                ),
            }
        if result == "missing":
            return {
                "status": "missing",
                "record": None,
                "error": _error("user_config_not_found", "User config was not found."),
            }
        return {
            "status": "unavailable",
            "record": None,
            "error": _error(
                "user_config_store_unavailable",
                "User config storage returned an invalid conditional write response.",
            ),
        }


    def read_user_config_for_authenticated_user(
        headers,
    ) -> tuple[AuthenticatedUserContext | None, UserConfigReadResult]:
        user, auth_error = resolve_authenticated_user(headers)
        if auth_error or not user:
            return None, {
                "status": "unavailable"
                if auth_error and auth_error["code"] == "session_auth_unavailable"
                else "unauthorized",
                "config": None,
                "error": auth_error,
            }

        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return user, {"status": "unavailable", "config": None, "error": store_error}

        return user, read_user_config_record(store, user["email"])


    def _managed_inbox_error(
        status: Literal["malformed", "not_found"],
        code: UserConfigAccessErrorCode,
        message: str,
    ) -> OwnedManagedInboxResult:
        return {"status": status, "inbox": None, "error": _error(code, message)}


    def resolve_managed_inbox(
        config: dict,
        mailbox_id: str,
    ) -> OwnedManagedInboxResult:
        if (
            not isinstance(mailbox_id, str)
            or not mailbox_id
            or mailbox_id != mailbox_id.strip()
        ):
            return _managed_inbox_error(
                "malformed",
                "invalid_mailbox_id",
                "Mailbox id must be a non-empty exact string.",
            )

        if not isinstance(config, dict):
            return _managed_inbox_error(
                "malformed",
                "user_config_malformed",
                "User config record is malformed.",
            )

        managed_inboxes = config.get("managedInboxes")
        if not isinstance(managed_inboxes, list):
            return _managed_inbox_error(
                "malformed",
                "managed_inbox_malformed",
                "Managed inbox configuration is malformed.",
            )

        matches: list[dict] = []
        for inbox in managed_inboxes:
            if not isinstance(inbox, dict):
                return _managed_inbox_error(
                    "malformed",
                    "managed_inbox_malformed",
                    "Managed inbox configuration is malformed.",
                )

            stored_id = inbox.get("id")
            if (
                not isinstance(stored_id, str)
                or not stored_id
                or stored_id != stored_id.strip()
            ):
                return _managed_inbox_error(
                    "malformed",
                    "managed_inbox_malformed",
                    "Managed inbox configuration contains an invalid id.",
                )

            if stored_id == mailbox_id:
                matches.append(inbox)

        if not matches:
            return _managed_inbox_error(
                "not_found",
                "managed_inbox_not_found",
                "Managed inbox was not found.",
            )

        if len(matches) > 1:
            return _managed_inbox_error(
                "malformed",
                "duplicate_mailbox_id",
                "Managed inbox configuration contains duplicate ids.",
            )

        inbox = matches[0]
        email = inbox.get("email")
        provider = inbox.get("provider")
        connected = inbox.get("connected")
        connection_status = inbox.get("connectionStatus")
        if (
            not isinstance(email, str)
            or (provider is not None and not isinstance(provider, str))
            or not isinstance(connected, bool)
            or not isinstance(connection_status, str)
        ):
            return _managed_inbox_error(
                "malformed",
                "managed_inbox_malformed",
                "Managed inbox configuration is malformed.",
            )

        return {
            "status": "ok",
            "inbox": {
                "id": mailbox_id,
                "email": email,
                "provider": provider,
                "connected": connected,
                "connectionStatus": connection_status,
            },
            "error": None,
        }


    def resolve_owned_managed_inbox(
        headers,
        mailbox_id: str,
    ) -> OwnedManagedInboxResult:
        user, read_result = read_user_config_for_authenticated_user(headers)
        if not user:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "unauthorized",
                "inbox": None,
                "error": read_result["error"],
            }

        if read_result["status"] == "missing":
            return {
                "status": "not_found",
                "inbox": None,
                "error": read_result["error"],
            }

        if read_result["status"] != "ok" or not read_result["config"]:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "malformed",
                "inbox": None,
                "error": read_result["error"],
            }

        config = read_result["config"]
        stored_owner_email = config.get("email")
        if stored_owner_email is not None and (
            not isinstance(stored_owner_email, str)
            or normalize_auth_email(stored_owner_email) != user["email"]
        ):
            return _managed_inbox_error(
                "malformed",
                "user_config_malformed",
                "User config ownership could not be verified.",
            )

        return resolve_managed_inbox(config, mailbox_id)


    def resolve_owned_managed_inbox_record(
        headers,
        mailbox_id: str,
    ) -> OwnedManagedInboxRecordResult:
        """Resolve a full mailbox record without changing the Gmail-safe helper above."""
        user, read_result = read_user_config_for_authenticated_user(headers)
        if not user:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "unauthorized",
                "user": None,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }

        if read_result["status"] == "missing":
            return {
                "status": "not_found",
                "user": user,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }
        if read_result["status"] != "ok" or not read_result["config"]:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }

        config = read_result["config"]
        stored_owner_email = config.get("email")
        if stored_owner_email is not None and (
            not isinstance(stored_owner_email, str)
            or normalize_auth_email(stored_owner_email) != user["email"]
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "user_config_malformed",
                    "User config ownership could not be verified.",
                ),
            }

        minimal_result = resolve_managed_inbox(config, mailbox_id)
        if minimal_result["status"] != "ok":
            return {
                "status": minimal_result["status"],
                "user": user,
                "inbox": None,
                "config": config,
                "error": minimal_result["error"],
            }

        matching_inbox = next(
            inbox
            for inbox in config["managedInboxes"]
            if isinstance(inbox, dict) and inbox.get("id") == mailbox_id
        )
        return {
            "status": "ok",
            "user": user,
            "inbox": deepcopy(matching_inbox),
            "config": deepcopy(config),
            "error": None,
        }


    def _valid_onboarding_inbox_id(value: object) -> bool:
        return (
            isinstance(value, str)
            and value == value.strip()
            and bool(value)
            and len(value) <= MAX_ONBOARDING_INBOX_ID_LENGTH
            and (
                value in ONBOARDING_PRESET_INBOX_IDS
                or ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(value) is not None
            )
        )


    def _onboarding_registration_status(
        error: UserConfigAccessError,
    ) -> Literal["not_found", "malformed", "conflict", "unavailable"]:
        if error["code"] == "user_config_store_unavailable":
            return "unavailable"
        if error["code"] == "onboarding_unavailable":
            return "not_found"
        if error["code"] in {"onboarding_malformed", "unknown_inbox_position"}:
            return "malformed"
        return "conflict"


    def _resolve_authoritative_onboarding_session(
        config: dict,
    ) -> tuple[dict | None, UserConfigAccessError | None]:
        """Reuse the canonical user-config classifier without creating an import cycle."""
        if not isinstance(config, dict):
            return None, _error("onboarding_malformed", "Onboarding state is invalid.")
        onboarding = config.get("onboardingSession")
        if onboarding is None or onboarding == {}:
            return None, _error("onboarding_unavailable", "Onboarding is unavailable.")
        try:
            from .user.config import _classify_stored_onboarding_session

            state, normalized = _classify_stored_onboarding_session(onboarding)
        except Exception:
            return None, _error(
                "user_config_store_unavailable",
                "Onboarding validation is temporarily unavailable.",
            )
        if getattr(state, "value", None) != "valid" or not isinstance(normalized, dict):
            return None, _error("onboarding_malformed", "Onboarding state is invalid.")
        return normalized, None


    def _validate_onboarding_imap_registration(
        config: dict,
        onboarding_inbox_id: str,
        mailbox_email: str,
    ) -> UserConfigAccessError | None:
        """Validate an IMAP target against the canonical authoritative draft."""
        onboarding, onboarding_error = _resolve_authoritative_onboarding_session(config)
        if onboarding_error or not onboarding:
            return onboarding_error or _error(
                "onboarding_malformed",
                "Onboarding state is invalid.",
            )
        if onboarding["completed"] is True:
            return _error("onboarding_completed", "Onboarding is already completed.")
        if onboarding["completed"] is not False:
            return _error("onboarding_malformed", "Onboarding state is invalid.")

        choices = onboarding["choices"]
        custom_inboxes = choices.get("customInboxes", [])
        custom_ids = {
            item["id"]
            for item in custom_inboxes
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        selected_inboxes = choices.get("selectedInboxes")
        if not isinstance(selected_inboxes, list):
            return _error("onboarding_malformed", "Onboarding state is invalid.")

        if (
            not _valid_onboarding_inbox_id(onboarding_inbox_id)
            or (
                onboarding_inbox_id.startswith("custom:")
                and onboarding_inbox_id not in custom_ids
            )
        ):
            return _error("unknown_inbox_position", "Inbox position is invalid.")
        if onboarding_inbox_id not in selected_inboxes:
            return _error(
                "inbox_position_not_selected",
                "Inbox position is not selected in onboarding.",
            )

        normalized_email = normalize_auth_email(mailbox_email)
        if not normalized_email or not is_valid_auth_email(normalized_email):
            return _error("onboarding_malformed", "Mailbox identity is invalid.")

        managed_inboxes = config.get("managedInboxes")
        if not isinstance(managed_inboxes, list):
            return _error("onboarding_malformed", "Mailbox configuration is invalid.")

        seen_ids: set[str] = set()
        for inbox in managed_inboxes:
            if not isinstance(inbox, dict):
                return _error("onboarding_malformed", "Mailbox configuration is invalid.")
            stored_id = inbox.get("id")
            stored_email = inbox.get("email")
            if (
                not isinstance(stored_id, str)
                or not stored_id
                or stored_id != stored_id.strip()
                or stored_id.casefold() in seen_ids
                or not isinstance(stored_email, str)
                or not is_valid_auth_email(stored_email)
            ):
                return _error("onboarding_malformed", "Mailbox configuration is invalid.")
            seen_ids.add(stored_id.casefold())

            stored_position = inbox.get("onboardingInboxId")
            if stored_position is not None and (
                not isinstance(stored_position, str)
                or stored_position != stored_position.strip()
                or not _valid_onboarding_inbox_id(stored_position)
                or (
                    stored_position.startswith("custom:")
                    and stored_position not in custom_ids
                )
            ):
                return _error("onboarding_malformed", "Mailbox configuration is invalid.")
            if stored_position == onboarding_inbox_id:
                return _error(
                    "inbox_position_conflict",
                    "Inbox position is already connected.",
                )
            if normalize_auth_email(stored_email) == normalized_email:
                return _error(
                    "mailbox_email_conflict",
                    "Mailbox is already registered.",
                )

        return None


    def resolve_owned_onboarding_imap_registration(
        headers,
        onboarding_inbox_id: str,
        mailbox_email: str,
    ) -> OwnedManagedInboxRecordResult:
        """Resolve one server-owned onboarding target before any IMAP action."""
        user, read_result = read_user_config_for_authenticated_user(headers)
        if not user:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "unauthorized",
                "user": None,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }
        if read_result["status"] == "missing":
            return {
                "status": "not_found",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error("onboarding_unavailable", "Onboarding is unavailable."),
            }
        if read_result["status"] != "ok" or not read_result["config"]:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }

        config = read_result["config"]
        stored_owner = config.get("email")
        if stored_owner is not None and (
            not isinstance(stored_owner, str)
            or normalize_auth_email(stored_owner) != user["email"]
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "user_config_malformed",
                    "User config ownership could not be verified.",
                ),
            }

        validation_error = _validate_onboarding_imap_registration(
            config,
            onboarding_inbox_id,
            mailbox_email,
        )
        if validation_error:
            return {
                "status": _onboarding_registration_status(validation_error),
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": validation_error,
            }
        return {
            "status": "ok",
            "user": user,
            "inbox": None,
            "config": deepcopy(config),
            "error": None,
        }


    def resolve_owned_initial_imap_registration(headers) -> OwnedManagedInboxRecordResult:
        """Allow the legacy settings flow only after authoritative onboarding completion."""
        user, read_result = read_user_config_for_authenticated_user(headers)
        if not user:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "unauthorized",
                "user": None,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }
        if read_result["status"] == "missing":
            return {
                "status": "not_found",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error("onboarding_unavailable", "Onboarding is unavailable."),
            }
        if read_result["status"] != "ok" or not read_result["config"]:
            return {
                "status": "unavailable"
                if read_result["status"] == "unavailable"
                else "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }

        config = read_result["config"]
        stored_owner = config.get("email")
        if stored_owner is not None and (
            not isinstance(stored_owner, str)
            or normalize_auth_email(stored_owner) != user["email"]
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "user_config_malformed",
                    "User config ownership could not be verified.",
                ),
            }

        onboarding, onboarding_error = _resolve_authoritative_onboarding_session(config)
        if onboarding_error or not onboarding:
            error = onboarding_error or _error(
                "onboarding_malformed",
                "Onboarding state is invalid.",
            )
            return {
                "status": _onboarding_registration_status(error),
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": error,
            }
        if onboarding.get("completed") is not True:
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": _error(
                    "onboarding_incomplete",
                    "A server-authoritative onboarding request is required.",
                ),
            }
        return {
            "status": "ok",
            "user": user,
            "inbox": None,
            "config": deepcopy(config),
            "error": None,
        }


    def upsert_owned_custom_imap_mailbox(
        headers,
        mailbox_id: str,
        mode: Literal["initial", "reconnect"],
        connection_metadata: dict,
        approved_updates: dict | None = None,
        *,
        credential_version: str,
        expected_inbox: dict | None = None,
        onboarding_inbox_id: str | None = None,
        require_completed_onboarding: bool = False,
        _write_attempts_remaining: int = MAX_CUSTOM_IMAP_CONFIG_WRITE_ATTEMPTS,
    ) -> OwnedManagedInboxRecordResult:
        """Persist non-secret connection metadata in the authenticated user's config."""
        user, auth_error = resolve_authenticated_user(headers)
        if auth_error or not user:
            return {
                "status": "unavailable"
                if auth_error and auth_error["code"] == "session_auth_unavailable"
                else "unauthorized",
                "user": None,
                "inbox": None,
                "config": None,
                "error": auth_error,
            }
        if (
            mode not in {"initial", "reconnect"}
            or not isinstance(mailbox_id, str)
            or not mailbox_id
            or mailbox_id != mailbox_id.strip()
            or mailbox_id.startswith("draft-")
            or not isinstance(connection_metadata, dict)
            or not is_valid_mailbox_credential_version(credential_version)
            or _contains_mailbox_credential_generation(connection_metadata)
            or _contains_mailbox_credential_generation(approved_updates)
            or (mode == "initial" and expected_inbox is not None)
            or (mode == "reconnect" and not isinstance(expected_inbox, dict))
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error("invalid_mailbox_id", "Mailbox id is invalid."),
            }
        connection_metadata = deepcopy(connection_metadata)
        if require_completed_onboarding and (
            mode != "initial" or onboarding_inbox_id is not None
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "onboarding_malformed",
                    "Mailbox registration mode is invalid.",
                ),
            }
        if onboarding_inbox_id is not None:
            normalized_email = normalize_auth_email(
                connection_metadata.get("email")
                if isinstance(connection_metadata.get("email"), str)
                else ""
            )
            if (
                mode != "initial"
                or not _valid_onboarding_inbox_id(onboarding_inbox_id)
                or not normalized_email
                or not is_valid_auth_email(normalized_email)
                or (
                    "onboardingInboxId" in connection_metadata
                    and connection_metadata.get("onboardingInboxId")
                    != onboarding_inbox_id
                )
            ):
                return {
                    "status": "malformed",
                    "user": user,
                    "inbox": None,
                    "config": None,
                    "error": _error(
                        "onboarding_malformed",
                        "Onboarding mailbox registration is invalid.",
                    ),
                }
            connection_metadata["email"] = normalized_email
            connection_metadata["onboardingInboxId"] = onboarding_inbox_id

        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return {
                "status": "unavailable",
                "user": user,
                "inbox": None,
                "config": None,
                "error": store_error,
            }

        read_result = read_user_config_record(store, user["email"])
        if read_result["status"] in {"unavailable", "malformed"}:
            return {
                "status": read_result["status"],
                "user": user,
                "inbox": None,
                "config": None,
                "error": read_result["error"],
            }

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if read_result["status"] == "missing" and onboarding_inbox_id is not None:
            return {
                "status": "not_found",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error("onboarding_unavailable", "Onboarding is unavailable."),
            }
        if read_result["status"] == "missing":
            config: dict = {
                "v": USER_CONFIG_SCHEMA_VERSION,
                "email": user["email"],
                "updatedAt": now,
                "managedInboxes": [],
            }
        else:
            config = deepcopy(read_result["config"])
            stored_owner = config.get("email")
            if stored_owner is not None and (
                not isinstance(stored_owner, str)
                or normalize_auth_email(stored_owner) != user["email"]
            ):
                return {
                    "status": "malformed",
                    "user": user,
                    "inbox": None,
                    "config": None,
                    "error": _error(
                        "user_config_malformed",
                        "User config ownership could not be verified.",
                    ),
                }

        expected_config = deepcopy(config)
        if require_completed_onboarding:
            onboarding, onboarding_error = _resolve_authoritative_onboarding_session(config)
            if onboarding_error or not onboarding:
                error = onboarding_error or _error(
                    "onboarding_malformed",
                    "Onboarding state is invalid.",
                )
                return {
                    "status": _onboarding_registration_status(error),
                    "user": user,
                    "inbox": None,
                    "config": deepcopy(config),
                    "error": error,
                }
            if onboarding.get("completed") is not True:
                return {
                    "status": "conflict",
                    "user": user,
                    "inbox": None,
                    "config": deepcopy(config),
                    "error": _error(
                        "onboarding_incomplete",
                        "A server-authoritative onboarding request is required.",
                    ),
                }

        if onboarding_inbox_id is not None:
            validation_error = _validate_onboarding_imap_registration(
                config,
                onboarding_inbox_id,
                connection_metadata["email"],
            )
            if validation_error:
                return {
                    "status": _onboarding_registration_status(validation_error),
                    "user": user,
                    "inbox": None,
                    "config": deepcopy(config),
                    "error": validation_error,
                }

        managed_inboxes = config.get("managedInboxes")
        if not isinstance(managed_inboxes, list) or any(
            not isinstance(inbox, dict) for inbox in managed_inboxes
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "managed_inbox_malformed",
                    "Managed inbox configuration is malformed.",
                ),
            }

        matching_indexes = [
            index
            for index, inbox in enumerate(managed_inboxes)
            if inbox.get("id") == mailbox_id
        ]
        if onboarding_inbox_id is not None and any(
            isinstance(inbox.get("id"), str)
            and inbox["id"].casefold() == mailbox_id.casefold()
            for inbox in managed_inboxes
        ):
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": _error(
                    "mailbox_id_conflict",
                    "A managed inbox with this id already exists.",
                ),
            }
        if len(matching_indexes) > 1:
            if mode == "initial":
                return {
                    "status": "conflict",
                    "user": user,
                    "inbox": None,
                    "config": deepcopy(config),
                    "error": _error(
                        "mailbox_id_conflict",
                        "A managed inbox with this id already exists.",
                    ),
                }
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error(
                    "duplicate_mailbox_id",
                    "Managed inbox configuration contains duplicate ids.",
                ),
            }

        if mode == "initial" and matching_indexes:
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": _error(
                    "mailbox_id_conflict",
                    "A managed inbox with this id already exists.",
                ),
            }
        if mode == "reconnect" and not matching_indexes:
            return {
                "status": "not_found",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": _error(
                    "managed_inbox_not_found",
                    "The reconnect target was not found.",
                ),
            }
        if (
            mode == "reconnect"
            and managed_inboxes[matching_indexes[0]].get("provider") != "custom_imap"
        ):
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": _error(
                    "managed_inbox_provider_mismatch",
                    "Only an existing Custom IMAP mailbox can be reconnected.",
                ),
            }

        existing = (
            deepcopy(managed_inboxes[matching_indexes[0]]) if matching_indexes else {}
        )
        if mode == "reconnect" and not _json_values_are_type_exact(
            existing,
            expected_inbox,
        ):
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(expected_config),
                "error": _error(
                    "user_config_write_conflict",
                    "Mailbox configuration changed before reconnect could be committed.",
                ),
            }
        next_inbox = {
            **existing,
            **deepcopy(connection_metadata),
            MAILBOX_CREDENTIAL_VERSION_FIELD: credential_version,
            "id": mailbox_id,
            "title": existing.get("title")
            or connection_metadata.get("email")
            or "Custom Inbox",
            "provider": "custom_imap",
            "connected": True,
            "connectionMethod": "imap",
            "connectionStatus": "connected",
            "connectionMessage": None,
            "oauthAuthorizationUrl": None,
        }
        for field in ("internalRole", "focusPreferences"):
            if approved_updates and field in approved_updates:
                next_inbox[field] = deepcopy(approved_updates[field])

        for settings_name in ("customImap", "customSmtp"):
            settings = next_inbox.get(settings_name)
            if isinstance(settings, dict):
                settings = deepcopy(settings)
                settings.pop("password", None)
                next_inbox[settings_name] = settings

        if matching_indexes:
            managed_inboxes[matching_indexes[0]] = next_inbox
        else:
            managed_inboxes.append(next_inbox)

        config["v"] = USER_CONFIG_SCHEMA_VERSION
        config["email"] = user["email"]
        config["updatedAt"] = now
        config = _strip_known_mailbox_passwords(config)
        write_result = (
            write_user_config_record_if_unchanged(
                store,
                user["email"],
                expected_config,
                config,
            )
            if read_result["status"] == "ok"
            else write_user_config_record_if_missing(store, user["email"], config)
        )
        if write_result["status"] in {"conflict", "missing"}:
            if _write_attempts_remaining > 1:
                return upsert_owned_custom_imap_mailbox(
                    headers,
                    mailbox_id,
                    mode,
                    connection_metadata,
                    approved_updates,
                    credential_version=credential_version,
                    expected_inbox=expected_inbox,
                    onboarding_inbox_id=onboarding_inbox_id,
                    require_completed_onboarding=require_completed_onboarding,
                    _write_attempts_remaining=_write_attempts_remaining - 1,
                )
            return {
                "status": "conflict",
                "user": user,
                "inbox": None,
                "config": deepcopy(expected_config),
                "error": write_result["error"]
                or _error(
                    "user_config_write_conflict",
                    "User config changed before the mailbox update could be committed.",
                ),
            }
        if write_result["status"] != "ok":
            return {
                "status": "unavailable",
                "user": user,
                "inbox": None,
                "config": deepcopy(config),
                "error": write_result["error"],
            }

        return {
            "status": "ok",
            "user": user,
            "inbox": deepcopy(next_inbox),
            "config": deepcopy(config),
            "error": None,
        }


    def _classify_custom_imap_rollback_target(
        config: dict,
        mailbox_id: str,
        expected_inbox: dict,
        previous_inbox: dict | None,
    ) -> tuple[Literal["applied", "expected", "newer", "ambiguous"], int | None]:
        """Classify only the target mailbox, ignoring unrelated config changes."""
        managed_inboxes = config.get("managedInboxes")
        if not isinstance(managed_inboxes, list) or any(
            not isinstance(inbox, dict) for inbox in managed_inboxes
        ):
            return "ambiguous", None

        matching_indexes = [
            index
            for index, inbox in enumerate(managed_inboxes)
            if isinstance(inbox.get("id"), str)
            and inbox["id"].casefold() == mailbox_id.casefold()
        ]
        if not matching_indexes:
            if previous_inbox is None:
                return "applied", None
            return "newer", None
        if len(matching_indexes) != 1:
            return "ambiguous", None

        index = matching_indexes[0]
        current_inbox = managed_inboxes[index]
        if previous_inbox is not None and _json_values_are_type_exact(
            current_inbox,
            previous_inbox,
        ):
            return "applied", index
        if _json_values_are_type_exact(current_inbox, expected_inbox):
            return "expected", index

        current_generation = current_inbox.get(
            MAILBOX_CREDENTIAL_VERSION_FIELD
        )
        expected_generation = expected_inbox.get(
            MAILBOX_CREDENTIAL_VERSION_FIELD
        )
        if (
            current_inbox.get("id") == mailbox_id
            and current_inbox.get("provider") == "custom_imap"
            and is_valid_mailbox_credential_version(current_generation)
            and current_generation != expected_generation
        ):
            return "newer", index
        return "ambiguous", index


    def rollback_owned_custom_imap_mailbox_update(
        headers,
        mailbox_id: str,
        expected_inbox: dict,
        previous_inbox: dict | None,
    ) -> dict | None:
        """Restore only an exact mailbox generation through the existing config CAS."""
        user, auth_error = resolve_authenticated_user(headers)
        if auth_error or not user:
            return auth_error or _error(
                "user_config_store_unavailable",
                "Mailbox configuration could not be restored.",
            )
        if (
            not isinstance(mailbox_id, str)
            or not mailbox_id
            or mailbox_id != mailbox_id.strip()
            or not isinstance(expected_inbox, dict)
            or (previous_inbox is not None and not isinstance(previous_inbox, dict))
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration rollback state is invalid.",
            )

        safe_expected = _strip_known_mailbox_passwords(
            {"managedInboxes": [expected_inbox]}
        )["managedInboxes"][0]
        safe_previous = (
            _strip_known_mailbox_passwords({"managedInboxes": [previous_inbox]})[
                "managedInboxes"
            ][0]
            if isinstance(previous_inbox, dict)
            else None
        )
        if (
            safe_expected.get("id") != mailbox_id
            or safe_expected.get("provider") != "custom_imap"
            or not is_valid_mailbox_credential_version(
                safe_expected.get(MAILBOX_CREDENTIAL_VERSION_FIELD)
            )
            or (
                safe_previous is not None
                and (
                    safe_previous.get("id") != mailbox_id
                    or safe_previous.get("provider") != "custom_imap"
                    or (
                        MAILBOX_CREDENTIAL_VERSION_FIELD in safe_previous
                        and not is_valid_mailbox_credential_version(
                            safe_previous.get(MAILBOX_CREDENTIAL_VERSION_FIELD)
                        )
                    )
                )
            )
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration rollback state is invalid.",
            )

        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return store_error or _error(
                "user_config_store_unavailable",
                "Mailbox configuration could not be restored.",
            )

        for _ in range(4):
            read_result = read_user_config_record(store, user["email"])
            if read_result["status"] == "missing":
                if safe_previous is None:
                    return None
                return _error(
                    "user_config_newer_mailbox_preserved",
                    "The mailbox configuration was removed concurrently.",
                )
            if read_result["status"] != "ok" or not isinstance(
                read_result.get("config"), dict
            ):
                return read_result["error"] or _error(
                    "user_config_store_unavailable",
                    "Mailbox configuration could not be restored.",
                )
            current = read_result["config"]
            current_owner = current.get("email")
            if current_owner is not None and (
                not isinstance(current_owner, str)
                or normalize_auth_email(current_owner) != user["email"]
            ):
                return _error(
                    "user_config_malformed",
                    "Mailbox configuration ownership could not be verified.",
                )
            target_state, target_index = _classify_custom_imap_rollback_target(
                current,
                mailbox_id,
                safe_expected,
                safe_previous,
            )
            if target_state == "applied":
                return None
            if target_state == "newer":
                return _error(
                    "user_config_newer_mailbox_preserved",
                    "A newer mailbox configuration was preserved.",
                )
            if target_state != "expected" or target_index is None:
                return _error(
                    "user_config_rollback_ambiguous",
                    "Mailbox configuration rollback outcome is ambiguous.",
                )

            replacement = deepcopy(current)
            if safe_previous is None:
                del replacement["managedInboxes"][target_index]
            else:
                replacement["managedInboxes"][target_index] = deepcopy(
                    safe_previous
                )
            replacement = _strip_known_mailbox_passwords(replacement)
            write_result = write_user_config_record_if_unchanged(
                store,
                user["email"],
                current,
                replacement,
            )
            if write_result["status"] == "ok":
                return None
            if write_result["status"] in {"conflict", "missing"}:
                continue

            verification = read_user_config_record(store, user["email"])
            if verification.get("status") == "missing":
                if safe_previous is None:
                    return None
                return _error(
                    "user_config_newer_mailbox_preserved",
                    "The mailbox configuration was removed concurrently.",
                )
            verification_config = verification.get("config")
            if verification.get("status") != "ok" or not isinstance(
                verification_config,
                dict,
            ):
                return _error(
                    "user_config_rollback_ambiguous",
                    "Mailbox configuration rollback outcome is ambiguous.",
                )
            verification_owner = verification_config.get("email")
            if verification_owner is not None and (
                not isinstance(verification_owner, str)
                or normalize_auth_email(verification_owner) != user["email"]
            ):
                return _error(
                    "user_config_rollback_ambiguous",
                    "Mailbox configuration rollback outcome is ambiguous.",
                )
            verification_state, _ = _classify_custom_imap_rollback_target(
                verification_config,
                mailbox_id,
                safe_expected,
                safe_previous,
            )
            if verification_state == "applied":
                return None
            if verification_state == "newer":
                return _error(
                    "user_config_newer_mailbox_preserved",
                    "A newer mailbox configuration was preserved.",
                )
            return _error(
                "user_config_rollback_ambiguous",
                "Mailbox configuration rollback outcome is ambiguous.",
            )

        return _error(
            "user_config_rollback_ambiguous",
            "Mailbox configuration rollback outcome is ambiguous.",
        )


    def rollback_owned_onboarding_imap_registration(
        headers,
        mailbox_id: str,
        expected_inbox: dict,
        baseline_config: dict,
    ) -> dict | None:
        """Remove only this route's exact mailbox record with an atomic CAS loop."""
        user, auth_error = resolve_authenticated_user(headers)
        if auth_error or not user:
            return auth_error or _error(
                "user_config_store_unavailable",
                "Mailbox configuration could not be restored.",
            )
        if (
            not isinstance(mailbox_id, str)
            or not mailbox_id
            or mailbox_id != mailbox_id.strip()
            or not isinstance(expected_inbox, dict)
            or not isinstance(baseline_config, dict)
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration could not be restored.",
            )
        safe_expected_wrapper = _strip_known_mailbox_passwords(
            {"managedInboxes": [expected_inbox]}
        )
        safe_expected_inbox = safe_expected_wrapper["managedInboxes"][0]
        expected_email = safe_expected_inbox.get("email")
        if (
            safe_expected_inbox.get("id") != mailbox_id
            or safe_expected_inbox.get("provider") != "custom_imap"
            or not _valid_onboarding_inbox_id(
                safe_expected_inbox.get("onboardingInboxId")
            )
            or not isinstance(expected_email, str)
            or not is_valid_auth_email(normalize_auth_email(expected_email))
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration could not be restored.",
            )
        stored_owner = baseline_config.get("email")
        if stored_owner is not None and (
            not isinstance(stored_owner, str)
            or normalize_auth_email(stored_owner) != user["email"]
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration could not be restored.",
            )
        if not isinstance(baseline_config.get("onboardingSession"), dict) or not isinstance(
            baseline_config.get("managedInboxes"), list
        ):
            return _error(
                "user_config_malformed",
                "Mailbox configuration could not be restored.",
            )

        store, store_error = resolve_user_config_store()
        if store_error or not store:
            return store_error or _error(
                "user_config_store_unavailable",
                "Mailbox configuration could not be restored.",
            )
        safe_baseline = _strip_known_mailbox_passwords(baseline_config)
        safe_baseline["v"] = USER_CONFIG_SCHEMA_VERSION
        safe_baseline["email"] = user["email"]

        for _ in range(4):
            read_result = read_user_config_record(store, user["email"])
            if read_result["status"] == "missing":
                return None
            if read_result["status"] != "ok" or not isinstance(
                read_result.get("config"), dict
            ):
                return read_result["error"] or _error(
                    "user_config_store_unavailable",
                    "Mailbox configuration could not be restored.",
                )

            current = read_result["config"]
            current_owner = current.get("email")
            if current_owner is not None and (
                not isinstance(current_owner, str)
                or normalize_auth_email(current_owner) != user["email"]
            ):
                return _error(
                    "user_config_malformed",
                    "Mailbox configuration could not be restored.",
                )
            managed_inboxes = current.get("managedInboxes")
            if not isinstance(managed_inboxes, list) or any(
                not isinstance(inbox, dict) for inbox in managed_inboxes
            ):
                return _error(
                    "user_config_malformed",
                    "Mailbox configuration could not be restored.",
                )
            matching_indexes = [
                index
                for index, inbox in enumerate(managed_inboxes)
                if isinstance(inbox.get("id"), str)
                and inbox["id"].casefold() == mailbox_id.casefold()
            ]
            if not matching_indexes:
                return None
            if len(matching_indexes) != 1:
                return _error(
                    "user_config_malformed",
                    "Mailbox configuration could not be restored.",
                )
            target = managed_inboxes[matching_indexes[0]]
            if not _json_values_are_type_exact(target, safe_expected_inbox):
                return _error(
                    "user_config_write_conflict",
                    "Mailbox configuration changed before it could be restored.",
                )

            replacement = deepcopy(current)
            del replacement["managedInboxes"][matching_indexes[0]]
            comparable_replacement = deepcopy(replacement)
            comparable_baseline = deepcopy(safe_baseline)
            comparable_replacement.pop("updatedAt", None)
            comparable_baseline.pop("updatedAt", None)
            if _json_values_are_type_exact(
                comparable_replacement,
                comparable_baseline,
            ):
                replacement = deepcopy(safe_baseline)
            else:
                replacement = _strip_known_mailbox_passwords(replacement)

            write_result = write_user_config_record_if_unchanged(
                store,
                user["email"],
                current,
                replacement,
            )
            if write_result["status"] in {"ok", "missing"}:
                return None
            if write_result["status"] != "conflict":
                return write_result["error"] or _error(
                    "user_config_store_unavailable",
                    "Mailbox configuration could not be restored.",
                )

        return _error(
            "user_config_write_conflict",
            "Mailbox configuration changed before it could be restored.",
        )
