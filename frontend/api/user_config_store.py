from __future__ import annotations

import json
import os
from typing import Literal, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from beta_auth import (
    normalize_auth_email,
    parse_beta_session_token,
    read_beta_session_cookie,
    resolve_beta_session_secret,
)

USER_CONFIG_SCHEMA_VERSION = 1
USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"


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
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}, None
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else {}
        except json.JSONDecodeError:
            parsed_error = {}

        return None, _error(
            "user_config_store_unavailable",
            parsed_error.get("error")
            or parsed_error.get("message")
            or f"User config store request failed with HTTP {error.code}.",
        )
    except URLError as error:
        return None, _error(
            "user_config_store_unavailable",
            str(error.reason)
            if getattr(error, "reason", None)
            else "Could not reach the user config store.",
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

    return {
        "status": "ok",
        "record": payload if isinstance(payload, dict) else None,
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
