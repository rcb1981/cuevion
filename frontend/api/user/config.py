from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import (  # noqa: E402
    normalize_auth_email,
    parse_beta_session_token,
    read_beta_session_cookie,
    resolve_beta_session_secret,
)

USER_CONFIG_SCHEMA_VERSION = 1
USER_CONFIG_KEY_PREFIX = "cuevion:user:v1"

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "accesstoken",
    "authorization",
    "authorization_header",
    "auth_token",
    "authtoken",
    "id_token",
    "idtoken",
    "password",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session",
    "token",
}
MESSAGE_CACHE_FIELD_NAMES = {
    "archive",
    "archived",
    "attachment",
    "attachments",
    "body",
    "bodyhtml",
    "body_html",
    "content",
    "contentbase64",
    "draft",
    "drafts",
    "file",
    "filebytes",
    "filecontent",
    "filedata",
    "files",
    "inbox",
    "inboxes",
    "invite",
    "invites",
    "liveinboxsnapshots",
    "mailboxstore",
    "messages",
    "oauthcallback",
    "oauthcallbackstate",
    "readstate",
    "sent",
    "snapshot",
    "snapshots",
    "spam",
    "trash",
    "unread",
}
BLOCKED_FIELD_NAME_PATTERNS = {
    "archivemessage",
    "attachedfile",
    "attachment",
    "authheader",
    "authorization",
    "body",
    "bodyhtml",
    "bytes",
    "content",
    "invite",
    "liveinboxsnapshot",
    "mailboxstore",
    "oauthcallback",
    "password",
    "readstate",
    "secret",
    "sentmessage",
    "snapshot",
    "spammessage",
    "token",
    "trashmessage",
    "unread",
}
ALLOWED_CONFIG_FIELDS = {
    "onboardingSession",
    "managedInboxes",
    "mailboxTitleOverrides",
    "primaryManagedInboxId",
    "mailboxFocusPreferenceOverrides",
    "inboxSignatures",
    "smartFolders",
    "uiPreferences",
    "displayNameOverrides",
}


def _resolve_durable_store_config() -> dict | None:
    rest_url = os.getenv("KV_REST_API_URL", "").strip()
    rest_token = os.getenv("KV_REST_API_TOKEN", "").strip()

    if not rest_url or not rest_token:
        return None

    return {
        "rest_url": rest_url.rstrip("/"),
        "rest_token": rest_token,
    }


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
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
            "code": "user_config_store_unavailable",
            "message": (
                parsed_error.get("error")
                or parsed_error.get("message")
                or f"User config store request failed with HTTP {error.code}."
            ),
        }
    except URLError as error:
        return None, {
            "code": "user_config_store_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach the user config store."
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
            "code": "user_config_store_unavailable",
            "message": "User config store returned an unreadable response.",
        }

    result = payload.get("result")
    if result is None:
        return None, None

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None, {
                "code": "user_config_store_unavailable",
                "message": "User config store returned malformed JSON.",
            }
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


def _get_authenticated_user(headers) -> dict | None:
    if not resolve_beta_session_secret():
        return None

    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _build_user_config_key(email: str) -> str:
    return f"{USER_CONFIG_KEY_PREFIX}:{normalize_auth_email(email)}"


def _is_blocked_field_name(key: str) -> bool:
    compact_key = "".join(char for char in key.strip().lower() if char.isalnum())
    snake_key = "".join(
        char for char in key.strip().lower() if char.isalnum() or char == "_"
    )

    if snake_key in SENSITIVE_FIELD_NAMES or snake_key in MESSAGE_CACHE_FIELD_NAMES:
        return True

    if compact_key in SENSITIVE_FIELD_NAMES or compact_key in MESSAGE_CACHE_FIELD_NAMES:
        return True

    return any(pattern in compact_key for pattern in BLOCKED_FIELD_NAME_PATTERNS)


def _strip_sensitive_fields(value):
    if isinstance(value, list):
        return [_strip_sensitive_fields(item) for item in value]

    if isinstance(value, dict):
        stripped = {}
        for key, item in value.items():
            if not isinstance(key, str) or _is_blocked_field_name(key):
                continue
            stripped[key] = _strip_sensitive_fields(item)
        return stripped

    return copy.deepcopy(value)


def _sanitize_connection(value):
    if not isinstance(value, dict):
        return value

    sanitized = _strip_sensitive_fields(value)
    if not isinstance(sanitized, dict):
        return sanitized

    custom_imap = sanitized.get("customImap")
    if isinstance(custom_imap, dict):
        sanitized["customImap"] = {
            **custom_imap,
            "password": "",
        }

    custom_smtp = sanitized.get("customSmtp")
    if isinstance(custom_smtp, dict):
        sanitized["customSmtp"] = {
            **custom_smtp,
            "password": "",
        }

    if "oauthAuthorizationUrl" in sanitized:
        sanitized["oauthAuthorizationUrl"] = None

    return sanitized


def _sanitize_onboarding_session(value):
    if not isinstance(value, dict):
        return None

    sanitized = _strip_sensitive_fields(value)
    if not isinstance(sanitized, dict):
        return None

    state = sanitized.get("state")
    if isinstance(state, dict):
        connections = state.get("inboxConnections")
        if isinstance(connections, dict):
            state["inboxConnections"] = {
                key: _sanitize_connection(connection)
                for key, connection in connections.items()
                if isinstance(key, str)
            }

    return sanitized


def _sanitize_managed_inboxes(value):
    if not isinstance(value, list):
        return []

    return [
        _sanitize_connection(mailbox)
        for mailbox in value
        if isinstance(mailbox, dict)
    ]


def _sanitize_user_config(payload: dict, owner_email: str) -> dict:
    source_config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    sanitized: dict = {
        "v": USER_CONFIG_SCHEMA_VERSION,
        "email": normalize_auth_email(owner_email),
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    for key in ALLOWED_CONFIG_FIELDS:
        if key not in source_config:
            continue

        value = source_config[key]
        if key == "onboardingSession":
            sanitized_value = _sanitize_onboarding_session(value)
            if sanitized_value is not None:
                sanitized[key] = sanitized_value
        elif key == "managedInboxes":
            sanitized[key] = _sanitize_managed_inboxes(value)
        else:
            sanitized[key] = _strip_sensitive_fields(value)

    return sanitized


def _merge_user_config(existing_record: dict | None, sanitized_update: dict) -> dict:
    merged = {
        "v": USER_CONFIG_SCHEMA_VERSION,
        "email": sanitized_update["email"],
        "updatedAt": sanitized_update["updatedAt"],
        "onboardingSession": {},
        "managedInboxes": [],
        "mailboxTitleOverrides": {},
        "primaryManagedInboxId": None,
        "mailboxFocusPreferenceOverrides": {},
        "inboxSignatures": {},
        "smartFolders": [],
        "uiPreferences": {},
        "displayNameOverrides": {},
    }

    if isinstance(existing_record, dict):
        for key in ALLOWED_CONFIG_FIELDS:
            if key in existing_record:
                merged[key] = _strip_sensitive_fields(existing_record[key])

    for key, value in sanitized_update.items():
        merged[key] = value

    return merged


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        session_user = _get_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        config = _resolve_durable_store_config()
        if not config:
            _send_json(self, 200, {"ok": True, "config": None})
            return

        record, error = _read_durable_record(config, _build_user_config_key(session_user["email"]))
        if error:
            _send_json(self, 200, {"ok": True, "config": None})
            return

        _send_json(self, 200, {"ok": True, "config": record})

    def do_POST(self):
        session_user = _get_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        sanitized_config = _sanitize_user_config(payload or {}, session_user["email"])
        config = _resolve_durable_store_config()
        if not config:
            _send_json(
                self,
                503,
                _build_error("user_config_store_unavailable", "User config storage is not configured."),
            )
            return

        existing_record, _ = _read_durable_record(
            config,
            _build_user_config_key(session_user["email"]),
        )
        merged_config = _merge_user_config(existing_record, sanitized_config)

        _, store_error = _write_durable_record(
            config,
            _build_user_config_key(session_user["email"]),
            merged_config,
        )
        if store_error:
            _send_json(self, 503, {"ok": False, "error": store_error})
            return

        _send_json(self, 200, {"ok": True, "config": merged_config})

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
