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
    from copy import deepcopy
    from datetime import datetime, timezone
    from typing import Literal, TypedDict
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    from .beta_auth import (
        normalize_auth_email,
        parse_beta_session_token,
        read_beta_session_cookie,
        resolve_beta_session_secret,
    )

    USER_CONFIG_SCHEMA_VERSION = 1
    USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"
    MAX_USER_CONFIG_STORE_RESPONSE_BYTES = 256 * 1024


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
        if not resolve_beta_session_secret():
            return None, _error(
                "session_auth_unavailable",
                "Authenticated session validation is unavailable.",
            )

        session_token = read_beta_session_cookie(headers)
        if not session_token:
            return None, _error("missing_session", "An authenticated session is required.")

        session_user = parse_beta_session_token(session_token)
        if not session_user:
            return None, _error("invalid_session", "The authenticated session is invalid.")

        return {
            "email": session_user["email"],
            "name": session_user["name"],
            "userType": session_user["userType"],
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


    def upsert_owned_custom_imap_mailbox(
        headers,
        mailbox_id: str,
        mode: Literal["initial", "reconnect"],
        connection_metadata: dict,
        approved_updates: dict | None = None,
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
        ):
            return {
                "status": "malformed",
                "user": user,
                "inbox": None,
                "config": None,
                "error": _error("invalid_mailbox_id", "Mailbox id is invalid."),
            }

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
        next_inbox = {
            **existing,
            **deepcopy(connection_metadata),
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
        write_result = write_user_config_record(store, user["email"], config)
        if write_result["status"] != "ok":
            return {
                "status": "unavailable",
                "user": user,
                "inbox": None,
                "config": None,
                "error": write_result["error"],
            }

        return {
            "status": "ok",
            "user": user,
            "inbox": deepcopy(next_inbox),
            "config": deepcopy(config),
            "error": None,
        }
