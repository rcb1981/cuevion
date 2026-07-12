from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import (  # noqa: E402
    normalize_auth_email,
)
from user_config_store import (  # noqa: E402
    USER_CONFIG_SCHEMA_VERSION,
    read_user_config_record,
    resolve_authenticated_user,
    resolve_user_config_store,
    write_user_config_record,
)

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
        session_user, _ = resolve_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        store, _ = resolve_user_config_store()
        if not store:
            _send_json(self, 200, {"ok": True, "config": None})
            return

        read_result = read_user_config_record(store, session_user["email"])
        if read_result["status"] != "ok":
            _send_json(self, 200, {"ok": True, "config": None})
            return

        _send_json(self, 200, {"ok": True, "config": read_result["config"]})

    def do_POST(self):
        session_user, _ = resolve_authenticated_user(self.headers)
        if not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        sanitized_config = _sanitize_user_config(payload or {}, session_user["email"])
        store, _ = resolve_user_config_store()
        if not store:
            _send_json(
                self,
                503,
                _build_error("user_config_store_unavailable", "User config storage is not configured."),
            )
            return

        read_result = read_user_config_record(store, session_user["email"])
        existing_record = read_result["config"] if read_result["status"] == "ok" else None
        merged_config = _merge_user_config(existing_record, sanitized_config)

        write_result = write_user_config_record(
            store,
            session_user["email"],
            merged_config,
        )
        if write_result["status"] != "ok":
            _send_json(self, 503, {"ok": False, "error": write_result["error"]})
            return

        _send_json(self, 200, {"ok": True, "config": merged_config})

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
