from __future__ import annotations

import copy
import json
import sys
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
INBOXES_DIR = API_DIR / "inboxes"
if str(INBOXES_DIR) not in sys.path:
    sys.path.insert(0, str(INBOXES_DIR))

from api.auth.email_address import normalize_auth_email  # noqa: E402
from authenticated_gmail import validate_focus_preferences  # noqa: E402
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
SAFE_MANAGED_INBOX_PRESENTATION_FIELDS = {
    "title",
    "internalRole",
    "focusPreferences",
}
MAX_MANAGED_INBOX_TITLE_LENGTH = 160
SUPPORTED_INTERNAL_ROLES = {
    "management",
    "label_manager",
    "label_ar_manager",
    "ar_manager",
    "product_manager",
    "artist_manager",
    "dj",
    "producer",
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


def _sanitize_stored_user_config(record: dict) -> dict:
    sanitized = _strip_sensitive_fields(record)
    if not isinstance(sanitized, dict):
        return {}
    if "onboardingSession" in sanitized:
        sanitized["onboardingSession"] = _sanitize_onboarding_session(
            sanitized.get("onboardingSession")
        ) or {}
    if "managedInboxes" in sanitized:
        sanitized["managedInboxes"] = _sanitize_managed_inboxes(
            sanitized.get("managedInboxes")
        )
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

    if "managedInboxes" in sanitized_update:
        existing_inboxes = _sanitize_managed_inboxes(
            existing_record.get("managedInboxes")
            if isinstance(existing_record, dict)
            else None
        )
        requested_inboxes = sanitized_update.get("managedInboxes")
        merged["managedInboxes"] = _merge_server_owned_managed_inboxes(
            existing_inboxes,
            requested_inboxes if isinstance(requested_inboxes, list) else [],
        )

    return merged


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _validate_managed_inbox_presentation_field(
    field: str,
    value: object,
) -> tuple[bool, object]:
    if field == "title":
        if not isinstance(value, str):
            return False, None
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > MAX_MANAGED_INBOX_TITLE_LENGTH
            or _has_control_characters(normalized)
        ):
            return False, None
        return True, normalized

    if field == "internalRole":
        if value is None:
            return True, None
        if (
            not isinstance(value, str)
            or value not in SUPPORTED_INTERNAL_ROLES
            or _has_control_characters(value)
        ):
            return False, None
        return True, value

    if field == "focusPreferences":
        validated, error = validate_focus_preferences(value)
        return error is None and validated is not None, validated

    return False, None


def _merge_server_owned_managed_inboxes(existing: list, requested: list) -> list:
    """Apply presentation-only edits without changing mailbox authority.

    Existing records and their order are authoritative. A client snapshot cannot
    create, remove, reorder, claim, connect, or change the transport identity of
    a mailbox. Ambiguous duplicate ids fail closed by applying no edits.
    """

    next_inboxes = [copy.deepcopy(inbox) for inbox in existing]
    existing_indexes_by_id: dict[str, list[int]] = {}
    requested_by_id: dict[str, list[dict]] = {}

    for index, inbox in enumerate(existing):
        if not isinstance(inbox, dict):
            continue
        inbox_id = inbox.get("id")
        if not isinstance(inbox_id, str) or not inbox_id.strip():
            continue
        normalized_inbox_id = inbox_id.strip().casefold()
        existing_indexes_by_id.setdefault(normalized_inbox_id, []).append(index)

    for inbox in requested:
        if not isinstance(inbox, dict):
            continue
        inbox_id = inbox.get("id")
        if not isinstance(inbox_id, str) or not inbox_id.strip():
            continue
        normalized_inbox_id = inbox_id.strip().casefold()
        requested_by_id.setdefault(normalized_inbox_id, []).append(inbox)

    for normalized_inbox_id, existing_indexes in existing_indexes_by_id.items():
        requested_matches = requested_by_id.get(normalized_inbox_id, [])
        if len(existing_indexes) != 1 or len(requested_matches) != 1:
            continue

        existing_index = existing_indexes[0]
        preserved = copy.deepcopy(existing[existing_index])
        requested_inbox = requested_matches[0]
        for field in SAFE_MANAGED_INBOX_PRESENTATION_FIELDS:
            if field not in requested_inbox:
                continue
            is_valid, validated_value = _validate_managed_inbox_presentation_field(
                field,
                requested_inbox[field],
            )
            if is_valid:
                preserved[field] = copy.deepcopy(validated_value)
        next_inboxes[existing_index] = preserved

    return next_inboxes


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
        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not session_user:
            if auth_error and auth_error["code"] == "session_auth_unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "authentication_unavailable",
                        "Authentication is temporarily unavailable.",
                    ),
                )
            else:
                _send_json(
                    self,
                    401,
                    _build_error(
                        "unauthorized",
                        "A valid member session is required.",
                    ),
                )
            return

        store, _ = resolve_user_config_store()
        if not store:
            _send_json(self, 200, {"ok": True, "config": None})
            return

        read_result = read_user_config_record(store, session_user["email"])
        if read_result["status"] != "ok":
            _send_json(self, 200, {"ok": True, "config": None})
            return

        stored_config = read_result["config"]
        sanitized_config = _sanitize_stored_user_config(stored_config)
        if sanitized_config != stored_config:
            write_result = write_user_config_record(
                store,
                session_user["email"],
                sanitized_config,
            )
            if write_result["status"] != "ok":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "user_config_store_unavailable",
                        "User configuration could not be sanitized.",
                    ),
                )
                return

        _send_json(self, 200, {"ok": True, "config": sanitized_config})

    def do_POST(self):
        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not session_user:
            if auth_error and auth_error["code"] == "session_auth_unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "authentication_unavailable",
                        "Authentication is temporarily unavailable.",
                    ),
                )
            else:
                _send_json(
                    self,
                    401,
                    _build_error(
                        "unauthorized",
                        "A valid member session is required.",
                    ),
                )
            return

        payload, error = _read_json_body(self)
        if error:
            _send_json(self, 400, error)
            return

        store, _ = resolve_user_config_store()
        if not store:
            _send_json(
                self,
                503,
                _build_error("user_config_store_unavailable", "User config storage is not configured."),
            )
            return

        read_result = read_user_config_record(store, session_user["email"])
        read_status = read_result.get("status") if isinstance(read_result, dict) else None
        if read_status == "ok" and isinstance(read_result.get("config"), dict):
            existing_record = read_result["config"]
        elif read_status == "missing":
            existing_record = None
        else:
            _send_json(
                self,
                503,
                _build_error(
                    "user_config_store_unavailable",
                    "User config storage is temporarily unavailable.",
                ),
            )
            return

        sanitized_config = _sanitize_user_config(payload or {}, session_user["email"])
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
