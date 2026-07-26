from __future__ import annotations

import copy
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from enum import Enum
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
from mailbox_secret_store import (  # noqa: E402
    is_valid_mailbox_credential_version,
    read_mailbox_secret,
)
from oauth_token_store import load_google_token_record_with_metadata  # noqa: E402
from user_config_store import (  # noqa: E402
    USER_CONFIG_SCHEMA_VERSION,
    read_user_config_record,
    resolve_authenticated_user,
    resolve_user_config_store,
    write_user_config_record_if_missing,
    write_user_config_record_if_unchanged,
)

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "accesstoken",
    "authorization",
    "authorization_header",
    "auth_token",
    "authtoken",
    "ciphertext",
    "credential_id",
    "credential_record",
    "credential_ref",
    "credential_reference",
    "credential_references",
    "credentialid",
    "credentialrecord",
    "credentialref",
    "credentialreference",
    "credentialreferences",
    "encrypted_blob",
    "encryptedblob",
    "encryption_key",
    "encryption_keys",
    "encryptionkey",
    "encryptionkeys",
    "id_token",
    "idtoken",
    "key_material",
    "keymaterial",
    "nonce",
    "password",
    "private_key",
    "privatekey",
    "raw_credential_record",
    "raw_store_record",
    "rawcredentialrecord",
    "rawstorerecord",
    "refresh_token",
    "refreshtoken",
    "secret",
    "session",
    "store_record",
    "storerecord",
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
SERVER_ONLY_CREDENTIAL_MARKERS = {
    "credentialversion",
    "secretversion",
    "credentialgeneration",
    "secretgeneration",
    "credentialrevision",
    "secretrevision",
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
ONBOARDING_SESSION_SCHEMA_VERSION = 1
ONBOARDING_MIN_CURRENT_STEP = 0
ONBOARDING_MAX_CURRENT_STEP = 3
ONBOARDING_ROLE_IDS = {
    "label_ar_manager",
    "label_manager",
    "ar_manager",
    "dj",
    "producer",
    "dj_producer",
    "label_owner",
    "legal",
    "finance",
    "royalty",
    "sync_licensing",
    "social_media_manager",
    "promo_manager",
    "distribution",
    "admin",
}
ONBOARDING_PRESET_INBOX_IDS = {
    "main",
    "demo",
    "business",
    "promo",
    "legal",
    "finance",
    "royalty",
    "sync",
}
ONBOARDING_INBOX_COUNTS = {"1", "2", "3", "4+", "not_sure"}
ONBOARDING_PRIMARY_INBOX_TYPES = {"personal", "work"}
ONBOARDING_FOCUS_FIELDS = (
    "demos",
    "promo",
    "finance",
    "legal",
    "business",
    "updates",
    "distribution",
    "royalties",
    "promoReminders",
    "paymentReminders",
)
ONBOARDING_FOCUS_VALUES = {"medium", "low"}
ONBOARDING_CHOICE_FIELDS = (
    "primaryRole",
    "internalRole",
    "secondaryRole",
    "primaryInbox",
    "primaryInboxType",
    "focusPreferences",
    "inboxCount",
    "selectedInboxes",
    "customInboxes",
)
ONBOARDING_FORBIDDEN_EXACT_KEYS = {
    "account",
    "authorizationcode",
    "connected",
    "connectionstatus",
    "email",
    "host",
    "hostname",
    "inboxconnections",
    "oauthcode",
    "oauthstate",
    "owneremail",
    "port",
    "reconnectstatus",
    "state",
    "userid",
    "username",
    "workspaceid",
}
ONBOARDING_FORBIDDEN_KEY_PATTERNS = {
    "auth0",
    "authorization",
    "ciphertext",
    "connection",
    "credential",
    "email",
    "imap",
    "oauth",
    "password",
    "provider",
    "reconnect",
    "session",
    "smtp",
    "status",
    "token",
}
MAX_ONBOARDING_CUSTOM_INBOXES = 64
MAX_ONBOARDING_INBOX_ID_LENGTH = 160
MAX_ONBOARDING_INBOX_NAME_LENGTH = 160
MAX_USER_CONFIG_BODY_BYTES = 512 * 1024
MAX_USER_CONFIG_JSON_DEPTH = 32
MAX_USER_CONFIG_JSON_NODES = 8192
MAX_USER_CONFIG_CAS_ATTEMPTS = 3
ONBOARDING_CUSTOM_INBOX_ID_PATTERN = re.compile(
    r"^custom:[a-z0-9]+(?:-[a-z0-9]+)*$"
)


class _OnboardingSessionValidationError(ValueError):
    pass


class _StoredUserConfigValidationError(ValueError):
    pass


class _OnboardingCompletionError(ValueError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class _JsonStructureLimitError(ValueError):
    pass


class _StoredOnboardingSessionState(Enum):
    NOT_STARTED = "not_started"
    VALID = "valid"
    INVALID = "invalid"


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


def _is_server_only_credential_field_name(key: str) -> bool:
    compact_key = "".join(
        character for character in key.strip().lower() if character.isalnum()
    )
    return compact_key in SERVER_ONLY_CREDENTIAL_MARKERS or (
        ("credential" in compact_key or "secret" in compact_key)
        and (
            "version" in compact_key
            or "generation" in compact_key
            or "revision" in compact_key
        )
    )


def _contains_server_only_credential_field(value) -> bool:
    if isinstance(value, dict):
        return any(
            (
                isinstance(key, str)
                and _is_server_only_credential_field_name(key)
            )
            or _contains_server_only_credential_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_server_only_credential_field(item) for item in value)
    return False


def _strip_server_only_credential_fields(value):
    if isinstance(value, list):
        return [_strip_server_only_credential_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_server_only_credential_fields(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not _is_server_only_credential_field_name(key)
        }
    return copy.deepcopy(value)


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

    if "oauthAuthorizationUrl" in sanitized:
        sanitized["oauthAuthorizationUrl"] = None

    return sanitized


def _compact_field_name(key: str) -> str:
    return "".join(character for character in key.strip().lower() if character.isalnum())


def _iter_bounded_json_nodes(value):
    pending = [(value, 1)]
    visited_container_ids = set()
    node_count = 0

    while pending:
        current, depth = pending.pop()
        node_count += 1
        if node_count > MAX_USER_CONFIG_JSON_NODES:
            raise _JsonStructureLimitError
        if depth > MAX_USER_CONFIG_JSON_DEPTH:
            raise _JsonStructureLimitError

        if isinstance(current, dict):
            container_id = id(current)
            if container_id in visited_container_ids:
                raise _JsonStructureLimitError
            visited_container_ids.add(container_id)
            if any(not isinstance(key, str) for key in current):
                raise _JsonStructureLimitError
            pending.extend(
                (item, depth + 1)
                for item in reversed(tuple(current.values()))
            )
        elif isinstance(current, list):
            container_id = id(current)
            if container_id in visited_container_ids:
                raise _JsonStructureLimitError
            visited_container_ids.add(container_id)
            pending.extend((item, depth + 1) for item in reversed(current))

        yield current


def _validate_json_structure(value) -> None:
    for _ in _iter_bounded_json_nodes(value):
        pass


def _contains_forbidden_onboarding_key(value) -> bool:
    for current in _iter_bounded_json_nodes(value):
        if isinstance(current, dict):
            for key in current:
                if not isinstance(key, str):
                    return True
                compact_key = _compact_field_name(key)
                if compact_key in ONBOARDING_FORBIDDEN_EXACT_KEYS or any(
                    pattern in compact_key
                    for pattern in ONBOARDING_FORBIDDEN_KEY_PATTERNS
                ):
                    return True
    return False


def _validate_nullable_enum(value, allowed_values: set[str]):
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed_values:
        raise _OnboardingSessionValidationError
    return value


def _validate_onboarding_inbox_id(value) -> str:
    if not isinstance(value, str):
        raise _OnboardingSessionValidationError
    if value in ONBOARDING_PRESET_INBOX_IDS:
        return value
    if (
        len(value) > MAX_ONBOARDING_INBOX_ID_LENGTH
        or _has_control_characters(value)
        or ONBOARDING_CUSTOM_INBOX_ID_PATTERN.fullmatch(value) is None
    ):
        raise _OnboardingSessionValidationError
    return value


def _validate_onboarding_choice(field: str, value, *, allow_legacy_high: bool):
    if field in {"primaryRole", "secondaryRole"}:
        return _validate_nullable_enum(value, ONBOARDING_ROLE_IDS)
    if field == "internalRole":
        return _validate_nullable_enum(value, SUPPORTED_INTERNAL_ROLES)
    if field == "primaryInbox":
        return None if value is None else _validate_onboarding_inbox_id(value)
    if field == "primaryInboxType":
        return _validate_nullable_enum(value, ONBOARDING_PRIMARY_INBOX_TYPES)
    if field == "inboxCount":
        return _validate_nullable_enum(value, ONBOARDING_INBOX_COUNTS)

    if field == "focusPreferences":
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or key not in ONBOARDING_FOCUS_FIELDS
            for key in value
        ):
            raise _OnboardingSessionValidationError
        normalized_focus = {}
        for key in ONBOARDING_FOCUS_FIELDS:
            if key not in value:
                continue
            focus_value = value[key]
            if allow_legacy_high and focus_value == "high":
                focus_value = "medium"
            if (
                not isinstance(focus_value, str)
                or focus_value not in ONBOARDING_FOCUS_VALUES
            ):
                raise _OnboardingSessionValidationError
            normalized_focus[key] = focus_value
        return normalized_focus

    if field == "selectedInboxes":
        if not isinstance(value, list) or len(value) > MAX_ONBOARDING_CUSTOM_INBOXES:
            raise _OnboardingSessionValidationError
        normalized_ids = [_validate_onboarding_inbox_id(item) for item in value]
        if len(normalized_ids) != len(set(normalized_ids)):
            raise _OnboardingSessionValidationError
        return normalized_ids

    if field == "customInboxes":
        if not isinstance(value, list) or len(value) > MAX_ONBOARDING_CUSTOM_INBOXES:
            raise _OnboardingSessionValidationError
        normalized_custom_inboxes = []
        seen_ids = set()
        for custom_inbox in value:
            if not isinstance(custom_inbox, dict) or set(custom_inbox) != {"id", "name"}:
                raise _OnboardingSessionValidationError
            inbox_id = _validate_onboarding_inbox_id(custom_inbox["id"])
            if not inbox_id.startswith("custom:") or inbox_id in seen_ids:
                raise _OnboardingSessionValidationError
            name = custom_inbox["name"]
            if not isinstance(name, str):
                raise _OnboardingSessionValidationError
            normalized_name = name.strip()
            if (
                not normalized_name
                or len(normalized_name) > MAX_ONBOARDING_INBOX_NAME_LENGTH
                or _has_control_characters(normalized_name)
            ):
                raise _OnboardingSessionValidationError
            seen_ids.add(inbox_id)
            normalized_custom_inboxes.append({"id": inbox_id, "name": normalized_name})
        return normalized_custom_inboxes

    raise _OnboardingSessionValidationError


def _normalize_onboarding_choices(
    value,
    *,
    strict: bool,
    allow_legacy_high: bool = False,
) -> dict:
    if not isinstance(value, dict):
        raise _OnboardingSessionValidationError
    if strict and any(
        not isinstance(key, str) or key not in ONBOARDING_CHOICE_FIELDS
        for key in value
    ):
        raise _OnboardingSessionValidationError

    normalized = {}
    for field in ONBOARDING_CHOICE_FIELDS:
        if field not in value:
            continue
        try:
            normalized[field] = _validate_onboarding_choice(
                field,
                value[field],
                allow_legacy_high=allow_legacy_high,
            )
        except _OnboardingSessionValidationError:
            if strict:
                raise
    return normalized


def _validate_onboarding_session_write(value) -> dict:
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise _OnboardingSessionValidationError
    if set(value) != {"schemaVersion", "completed", "currentStep", "choices"}:
        raise _OnboardingSessionValidationError
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise _OnboardingSessionValidationError
    if value["completed"] is not False:
        raise _OnboardingSessionValidationError
    current_step = value["currentStep"]
    if (
        type(current_step) is not int
        or current_step < ONBOARDING_MIN_CURRENT_STEP
        or current_step > ONBOARDING_MAX_CURRENT_STEP
    ):
        raise _OnboardingSessionValidationError
    if not isinstance(value["choices"], dict):
        raise _OnboardingSessionValidationError
    try:
        contains_forbidden_key = _contains_forbidden_onboarding_key(value)
    except _JsonStructureLimitError as error:
        raise _OnboardingSessionValidationError from error
    if contains_forbidden_key:
        raise _OnboardingSessionValidationError

    return {
        "schemaVersion": ONBOARDING_SESSION_SCHEMA_VERSION,
        "completed": False,
        "currentStep": current_step,
        "choices": _normalize_onboarding_choices(value["choices"], strict=True),
    }


def _classify_stored_onboarding_session(
    value,
) -> tuple[_StoredOnboardingSessionState, dict | None]:
    if isinstance(value, dict) and not value:
        return _StoredOnboardingSessionState.NOT_STARTED, {}
    if not isinstance(value, dict):
        return _StoredOnboardingSessionState.INVALID, None

    try:
        _validate_json_structure(value)

        legacy_fields = set(value)
        if legacy_fields in (
            {"completed", "state"},
            {"completed", "completedAt", "state"},
        ):
            if value["completed"] is not True or not isinstance(value["state"], dict):
                raise _OnboardingSessionValidationError
            if "completedAt" in value and not isinstance(value["completedAt"], str):
                raise _OnboardingSessionValidationError
            if _contains_forbidden_onboarding_key(value["state"]):
                raise _OnboardingSessionValidationError
            normalized_choices = _normalize_onboarding_choices(
                value["state"],
                strict=True,
                allow_legacy_high=True,
            )
            return _StoredOnboardingSessionState.VALID, {
                "schemaVersion": ONBOARDING_SESSION_SCHEMA_VERSION,
                "completed": True,
                "currentStep": ONBOARDING_MAX_CURRENT_STEP,
                "choices": normalized_choices,
            }

        if set(value) != {"schemaVersion", "completed", "currentStep", "choices"}:
            raise _OnboardingSessionValidationError
        if (
            type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != ONBOARDING_SESSION_SCHEMA_VERSION
            or type(value["completed"]) is not bool
            or type(value["currentStep"]) is not int
            or value["currentStep"] < ONBOARDING_MIN_CURRENT_STEP
            or value["currentStep"] > ONBOARDING_MAX_CURRENT_STEP
            or not isinstance(value["choices"], dict)
            or _contains_forbidden_onboarding_key(value)
        ):
            raise _OnboardingSessionValidationError
        normalized_choices = _normalize_onboarding_choices(
            value["choices"],
            strict=True,
        )
        return _StoredOnboardingSessionState.VALID, {
            "schemaVersion": ONBOARDING_SESSION_SCHEMA_VERSION,
            "completed": value["completed"],
            "currentStep": value["currentStep"],
            "choices": normalized_choices,
        }
    except (_JsonStructureLimitError, _OnboardingSessionValidationError):
        return _StoredOnboardingSessionState.INVALID, None


def _normalize_stored_onboarding_session(value) -> dict:
    session_state, normalized = _classify_stored_onboarding_session(value)
    if session_state is _StoredOnboardingSessionState.INVALID or normalized is None:
        raise _StoredUserConfigValidationError
    return normalized


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
            sanitized[key] = _validate_onboarding_session_write(value)
        elif key == "managedInboxes":
            sanitized[key] = _sanitize_managed_inboxes(value)
        else:
            sanitized[key] = _strip_sensitive_fields(value)

    return sanitized


def _sanitize_stored_user_config(record: dict) -> dict:
    sanitized = _strip_server_only_credential_fields(
        _strip_sensitive_fields(record)
    )
    if not isinstance(sanitized, dict):
        return {}
    if "onboardingSession" in sanitized:
        sanitized["onboardingSession"] = _normalize_stored_onboarding_session(
            record.get("onboardingSession")
        )
    if "managedInboxes" in sanitized:
        sanitized["managedInboxes"] = _sanitize_managed_inboxes(
            sanitized.get("managedInboxes")
        )
    return sanitized


def _stored_mailbox_password_is_usable(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip().casefold()
    return (
        normalized
        not in {
            "stored securely",
            "stored securely — leave blank to reuse",
        }
        and re.fullmatch(r"[*•●]{6,}", normalized) is None
    )


def _without_empty_password_placeholders(value):
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if not (
            isinstance(key, str)
            and "password" in _compact_field_name(key)
            and item == ""
        )
    }


def _safe_imap_connection_config(value) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "host",
        "port",
        "ssl",
        "username",
    }:
        return False
    host = value.get("host")
    port = value.get("port")
    username = value.get("username")
    return bool(
        isinstance(host, str)
        and host
        and host == host.strip()
        and isinstance(port, str)
        and port == "993"
        and value.get("ssl") is True
        and isinstance(username, str)
        and username
        and username == username.strip()
    )


def _safe_smtp_submission_config(value) -> tuple[bool, str | None]:
    if not isinstance(value, dict) or not value:
        return False, None
    if set(value) != {
        "host",
        "port",
        "security",
        "username",
        "useSameCredentials",
    }:
        return False, None

    host = value.get("host")
    port = value.get("port")
    security = value.get("security")
    username = value.get("username")
    use_same_credentials = value.get("useSameCredentials")
    if (
        not isinstance(host, str)
        or not host
        or host != host.strip()
        or not isinstance(port, str)
        or port != port.strip()
        or not isinstance(username, str)
        or username != username.strip()
        or not isinstance(use_same_credentials, bool)
        or (not use_same_credentials and not username)
        or (security == "ssl" and port != "465")
        or (security == "starttls" and port != "587")
        or security not in {"ssl", "starttls"}
    ):
        return False, None
    return True, "imap" if use_same_credentials else "smtp"


def _enrich_public_custom_mailbox_capabilities(
    stored_config: dict,
    sanitized_config: dict,
    owner_email: str,
) -> dict:
    """Add generation-bound capability evidence without exposing generations."""
    stored_inboxes = stored_config.get("managedInboxes")
    public_inboxes = sanitized_config.get("managedInboxes")
    if not isinstance(stored_inboxes, list) or not isinstance(public_inboxes, list):
        return sanitized_config
    if len(stored_inboxes) != len(public_inboxes):
        return sanitized_config

    for stored_inbox, public_inbox in zip(stored_inboxes, public_inboxes):
        if (
            not isinstance(stored_inbox, dict)
            or not isinstance(public_inbox, dict)
            or stored_inbox.get("provider") != "custom_imap"
        ):
            continue

        mailbox_id = stored_inbox.get("id")
        config_generation = stored_inbox.get("credentialVersion")
        secret_result = None
        if (
            isinstance(mailbox_id, str)
            and mailbox_id
            and mailbox_id == mailbox_id.strip()
            and is_valid_mailbox_credential_version(config_generation)
        ):
            try:
                secret_result = read_mailbox_secret(owner_email, mailbox_id)
            except Exception:
                secret_result = None

        secret_record = (
            secret_result.get("record")
            if isinstance(secret_result, dict)
            and secret_result.get("status") == "present"
            and isinstance(secret_result.get("record"), dict)
            and secret_result.get("error") is None
            else None
        )
        generation_matches = (
            isinstance(secret_record, dict)
            and secret_record.get("credentialVersion") == config_generation
            and is_valid_mailbox_credential_version(
                secret_record.get("credentialVersion")
            )
        )
        imap_password_set = bool(
            generation_matches
            and _stored_mailbox_password_is_usable(
                secret_record.get("imapPassword") if secret_record else None
            )
        )
        incoming_status = stored_inbox.get("imapConnectionStatus")
        incoming_connected = bool(
            stored_inbox.get("connected") is True
            and stored_inbox.get("connectionStatus") == "connected"
            and incoming_status in {None, "connected"}
            and _safe_imap_connection_config(
                _without_empty_password_placeholders(
                    stored_inbox.get("customImap")
                )
            )
            and imap_password_set
        )

        smtp_configured, smtp_credential_source = _safe_smtp_submission_config(
            _without_empty_password_placeholders(stored_inbox.get("customSmtp"))
        )
        if "customSmtp" not in stored_inbox:
            public_inbox["customSmtp"] = {}
        smtp_password_set = bool(
            incoming_connected
            and generation_matches
            and smtp_configured
            and smtp_credential_source is not None
            and _stored_mailbox_password_is_usable(
                secret_record.get(
                    "imapPassword"
                    if smtp_credential_source == "imap"
                    else "smtpPassword"
                )
                if secret_record
                else None
            )
        )
        outgoing_connected = bool(
            smtp_password_set
            and stored_inbox.get("smtpConnectionStatus") == "connected"
            and stored_inbox.get("fullyConnected") is True
        )

        public_inbox["imapConnectionStatus"] = (
            "connected" if incoming_connected else "not_connected"
        )
        public_inbox["smtpConnectionStatus"] = (
            "connected"
            if outgoing_connected
            else "not_configured"
            if not smtp_configured
            else "not_connected"
        )
        public_inbox["imapPasswordSet"] = imap_password_set
        public_inbox["smtpPasswordSet"] = smtp_password_set
        public_inbox["fullyConnected"] = incoming_connected and outgoing_connected

    return sanitized_config


def _has_valid_known_stored_config_shapes(record: dict) -> bool:
    try:
        _validate_json_structure(record)
    except _JsonStructureLimitError:
        return False

    if "onboardingSession" not in record:
        return False

    if "v" in record and (
        type(record["v"]) not in (int, float)
        or record["v"] != record["v"]
        or record["v"] in (float("inf"), float("-inf"))
    ):
        return False
    for field in ("email", "updatedAt"):
        if field in record and not isinstance(record[field], str):
            return False

    if "onboardingSession" in record:
        session_state, _ = _classify_stored_onboarding_session(
            record["onboardingSession"]
        )
        if session_state is _StoredOnboardingSessionState.INVALID:
            return False

    managed_inboxes = record.get("managedInboxes")
    if "managedInboxes" in record and (
        not isinstance(managed_inboxes, list)
        or any(not isinstance(inbox, dict) for inbox in managed_inboxes)
    ):
        return False

    for field in (
        "mailboxTitleOverrides",
        "mailboxFocusPreferenceOverrides",
        "inboxSignatures",
    ):
        if field in record and not isinstance(record[field], dict):
            return False

    if "smartFolders" in record and not isinstance(record["smartFolders"], list):
        return False

    primary_managed_inbox_id = record.get("primaryManagedInboxId")
    if "primaryManagedInboxId" in record and not (
        primary_managed_inbox_id is None
        or isinstance(primary_managed_inbox_id, str)
    ):
        return False

    ui_preferences = record.get("uiPreferences")
    if "uiPreferences" in record:
        if not isinstance(ui_preferences, dict):
            return False
        theme_mode = ui_preferences.get("themeMode")
        if "themeMode" in ui_preferences and (
            not isinstance(theme_mode, str)
            or theme_mode
            not in {
                "Light",
                "Dark",
                "System",
                "light",
                "dark",
            }
        ):
            return False
        for field in (
            "aiSuggestionsEnabled",
            "inboxChangesEnabled",
            "teamActivityEnabled",
        ):
            if field in ui_preferences and type(ui_preferences[field]) is not bool:
                return False

    display_name_overrides = record.get("displayNameOverrides")
    if "displayNameOverrides" in record and (
        not isinstance(display_name_overrides, dict)
        or any(not isinstance(value, str) for value in display_name_overrides.values())
    ):
        return False

    return True


def _stored_config_owner_matches(record: dict, session_email: str) -> bool:
    if "email" not in record:
        return True

    stored_email = record.get("email")
    if not isinstance(stored_email, str) or not stored_email.strip():
        return False
    try:
        normalized_stored_email = normalize_auth_email(stored_email)
        normalized_session_email = normalize_auth_email(session_email)
    except Exception:
        return False
    return (
        isinstance(normalized_stored_email, str)
        and bool(normalized_stored_email)
        and normalized_stored_email == normalized_session_email
    )


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
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def _valid_server_managed_inbox_id(value) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and not value.startswith("draft-")
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
    )


def _completion_inboxes_incomplete() -> _OnboardingCompletionError:
    return _OnboardingCompletionError(
        409,
        "onboarding_mailboxes_incomplete",
        "One or more selected inboxes are not fully connected.",
    )


def _completion_dependency_unavailable() -> _OnboardingCompletionError:
    return _OnboardingCompletionError(
        503,
        "onboarding_completion_unavailable",
        "Onboarding completion could not be safely verified.",
    )


def _gmail_mailbox_is_authoritatively_ready(mailbox: dict, owner_email: str) -> bool:
    mailbox_email = mailbox.get("email")
    if (
        mailbox.get("provider") != "google"
        or mailbox.get("connectionMethod") != "oauth"
        or mailbox.get("connected") is not True
        or mailbox.get("connectionStatus") != "connected"
        or not isinstance(mailbox_email, str)
        or not mailbox_email
        or mailbox_email != mailbox_email.strip().lower()
    ):
        return False

    stored_oauth_owner = mailbox.get("oauthOwnerEmail")
    if stored_oauth_owner is not None:
        try:
            if normalize_auth_email(stored_oauth_owner) != normalize_auth_email(
                owner_email
            ):
                return False
        except Exception:
            return False

    try:
        token_record, token_error = load_google_token_record_with_metadata(
            mailbox_email
        )
    except Exception as error:
        raise _completion_dependency_unavailable() from error
    if token_error:
        raise _completion_dependency_unavailable()
    if not isinstance(token_record, dict):
        return False

    try:
        token_email = normalize_auth_email(str(token_record.get("email") or ""))
        token_owner = normalize_auth_email(
            str(token_record.get("owner_email") or "")
        )
        expected_owner = normalize_auth_email(owner_email)
    except Exception:
        return False
    if (
        token_record.get("provider") != "google"
        or token_email != mailbox_email
        or token_owner != expected_owner
        or token_record.get("_storage_durable") is not True
        or not _stored_mailbox_password_is_usable(
            token_record.get("access_token")
        )
    ):
        return False

    expires_at = token_record.get("expires_at")
    if expires_at is None:
        return True
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False
    try:
        parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if parsed_expiry.tzinfo is None:
            return False
    except ValueError:
        return False
    return (
        parsed_expiry > datetime.now(timezone.utc)
        or _stored_mailbox_password_is_usable(token_record.get("refresh_token"))
    )


def _custom_imap_mailbox_is_authoritatively_ready(
    mailbox: dict,
    owner_email: str,
) -> bool:
    if (
        mailbox.get("provider") != "custom_imap"
        or mailbox.get("connectionMethod") != "imap"
        or mailbox.get("connected") is not True
        or mailbox.get("connectionStatus") != "connected"
        or mailbox.get("imapConnectionStatus") != "connected"
        or mailbox.get("smtpConnectionStatus") != "connected"
        or mailbox.get("fullyConnected") is not True
        or not _safe_imap_connection_config(
            _without_empty_password_placeholders(mailbox.get("customImap"))
        )
    ):
        return False

    smtp_configured, smtp_credential_source = _safe_smtp_submission_config(
        _without_empty_password_placeholders(mailbox.get("customSmtp"))
    )
    credential_version = mailbox.get("credentialVersion")
    mailbox_id = mailbox.get("id")
    if (
        not smtp_configured
        or smtp_credential_source is None
        or not _valid_server_managed_inbox_id(mailbox_id)
        or not is_valid_mailbox_credential_version(credential_version)
    ):
        return False

    try:
        secret_result = read_mailbox_secret(owner_email, mailbox_id)
    except Exception as error:
        raise _completion_dependency_unavailable() from error
    if (
        isinstance(secret_result, dict)
        and secret_result.get("status") == "unavailable"
    ):
        raise _completion_dependency_unavailable()
    if (
        not isinstance(secret_result, dict)
        or secret_result.get("status") != "present"
        or secret_result.get("error") is not None
        or not isinstance(secret_result.get("record"), dict)
    ):
        return False

    secret_record = secret_result["record"]
    return bool(
        secret_record.get("credentialVersion") == credential_version
        and is_valid_mailbox_credential_version(
            secret_record.get("credentialVersion")
        )
        and _stored_mailbox_password_is_usable(secret_record.get("imapPassword"))
        and _stored_mailbox_password_is_usable(
            secret_record.get(
                "imapPassword"
                if smtp_credential_source == "imap"
                else "smtpPassword"
            )
        )
    )


def _build_authoritative_onboarding_completion(
    existing_record: dict,
    owner_email: str,
) -> dict:
    session_state, normalized_session = _classify_stored_onboarding_session(
        existing_record.get("onboardingSession")
    )
    if (
        session_state is not _StoredOnboardingSessionState.VALID
        or not isinstance(normalized_session, dict)
    ):
        raise _completion_inboxes_incomplete()

    choices = normalized_session.get("choices")
    selected_inboxes = (
        choices.get("selectedInboxes") if isinstance(choices, dict) else None
    )
    if (
        not isinstance(selected_inboxes, list)
        or not selected_inboxes
        or any(not isinstance(position, str) for position in selected_inboxes)
        or len(set(selected_inboxes)) != len(selected_inboxes)
    ):
        raise _completion_inboxes_incomplete()

    managed_inboxes = existing_record.get("managedInboxes")
    if not isinstance(managed_inboxes, list):
        raise _completion_inboxes_incomplete()

    selected_mailboxes = []
    for position in selected_inboxes:
        matches = [
            mailbox
            for mailbox in managed_inboxes
            if isinstance(mailbox, dict)
            and mailbox.get("onboardingInboxId") == position
        ]
        if len(matches) != 1:
            raise _completion_inboxes_incomplete()
        selected_mailboxes.append(matches[0])

    selected_ids = []
    for mailbox in selected_mailboxes:
        mailbox_id = mailbox.get("id")
        if not _valid_server_managed_inbox_id(mailbox_id):
            raise _completion_inboxes_incomplete()
        selected_ids.append(mailbox_id.casefold())
        if sum(
            1
            for candidate in managed_inboxes
            if isinstance(candidate, dict)
            and isinstance(candidate.get("id"), str)
            and candidate["id"].casefold() == mailbox_id.casefold()
        ) != 1:
            raise _completion_inboxes_incomplete()

        provider = mailbox.get("provider")
        if provider == "google":
            ready = _gmail_mailbox_is_authoritatively_ready(
                mailbox,
                owner_email,
            )
        elif provider == "custom_imap":
            ready = _custom_imap_mailbox_is_authoritatively_ready(
                mailbox,
                owner_email,
            )
        else:
            ready = False
        if not ready:
            raise _completion_inboxes_incomplete()

    if len(set(selected_ids)) != len(selected_ids):
        raise _completion_inboxes_incomplete()

    if (
        normalized_session.get("completed") is True
        and normalized_session.get("currentStep") == ONBOARDING_MAX_CURRENT_STEP
    ):
        return copy.deepcopy(existing_record)

    completed_record = copy.deepcopy(existing_record)
    completed_record["onboardingSession"] = {
        "schemaVersion": ONBOARDING_SESSION_SCHEMA_VERSION,
        "completed": True,
        "currentStep": ONBOARDING_MAX_CURRENT_STEP,
        "choices": copy.deepcopy(choices),
    }
    return completed_record


def _merge_user_config(existing_record: dict | None, sanitized_update: dict) -> dict:
    defaults = {
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
    merged = copy.deepcopy(existing_record) if isinstance(existing_record, dict) else {}
    for key, value in defaults.items():
        merged.setdefault(key, copy.deepcopy(value))

    existing_session_is_completed = False
    if isinstance(existing_record, dict):
        for key in ALLOWED_CONFIG_FIELDS:
            if key in existing_record:
                if key == "onboardingSession":
                    session_state, normalized_session = (
                        _classify_stored_onboarding_session(existing_record[key])
                    )
                    if session_state is _StoredOnboardingSessionState.INVALID:
                        raise _StoredUserConfigValidationError
                    merged[key] = copy.deepcopy(existing_record[key])
                    existing_session_is_completed = (
                        session_state is _StoredOnboardingSessionState.VALID
                        and isinstance(normalized_session, dict)
                        and normalized_session.get("completed") is True
                    )
                else:
                    merged[key] = _strip_sensitive_fields(existing_record[key])

    merged["v"] = USER_CONFIG_SCHEMA_VERSION
    merged["email"] = sanitized_update["email"]
    merged["updatedAt"] = sanitized_update["updatedAt"]

    if (
        "onboardingSession" in sanitized_update
        and existing_session_is_completed
    ):
        raise _OnboardingSessionValidationError

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


def _read_json_body(
    handler: BaseHTTPRequestHandler,
) -> tuple[dict | None, int | None, dict | None]:
    raw_content_length = handler.headers.get("content-length")
    if raw_content_length is None:
        content_length = 0
    else:
        try:
            if not isinstance(raw_content_length, str):
                raise ValueError
            normalized_content_length = raw_content_length.strip()
            if (
                not normalized_content_length.isascii()
                or not normalized_content_length.isdecimal()
            ):
                raise ValueError
            content_length = int(normalized_content_length, 10)
        except (ValueError, OverflowError):
            return (
                None,
                400,
                _build_error("invalid_request", "Content-Length header is invalid."),
            )

    if content_length > MAX_USER_CONFIG_BODY_BYTES:
        return (
            None,
            413,
            _build_error("request_body_too_large", "Request body is too large."),
        )

    try:
        raw_body_bytes = (
            handler.rfile.read(content_length) if content_length > 0 else b""
        )
        if len(raw_body_bytes) > MAX_USER_CONFIG_BODY_BYTES:
            return (
                None,
                413,
                _build_error("request_body_too_large", "Request body is too large."),
            )
        raw_body = raw_body_bytes.decode("utf-8")
        payload = json.loads(raw_body or "{}")
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        RecursionError,
        ValueError,
        OverflowError,
    ):
        return (
            None,
            400,
            _build_error("invalid_request", "Request body must be valid JSON."),
        )

    if not isinstance(payload, dict):
        return (
            None,
            400,
            _build_error("invalid_request", "Request body must be a JSON object."),
        )

    try:
        _validate_json_structure(payload)
    except _JsonStructureLimitError:
        return (
            None,
            400,
            _build_error(
                "invalid_json_structure",
                "Request body JSON structure is invalid.",
            ),
        )

    return payload, None, None


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

        if (
            not isinstance(session_user, dict)
            or session_user.get("userType") != "member"
        ):
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
            _send_json(
                self,
                503,
                _build_error(
                    "config_unavailable",
                    "User configuration is temporarily unavailable.",
                ),
            )
            return

        try:
            read_result = read_user_config_record(store, session_user["email"])
        except Exception:
            _send_json(
                self,
                503,
                _build_error(
                    "config_unavailable",
                    "User configuration is temporarily unavailable.",
                ),
            )
            return

        if not isinstance(read_result, dict):
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return

        read_status = read_result.get("status")
        stored_config = read_result.get("config")
        if read_status == "missing" and stored_config is None:
            _send_json(
                self,
                200,
                {"ok": True, "configState": "missing", "config": None},
            )
            return
        if read_status == "unavailable":
            _send_json(
                self,
                503,
                _build_error(
                    "config_unavailable",
                    "User configuration is temporarily unavailable.",
                ),
            )
            return
        if read_status != "ok" or not isinstance(stored_config, dict):
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return
        if not _has_valid_known_stored_config_shapes(stored_config):
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return
        if not _stored_config_owner_matches(stored_config, session_user["email"]):
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return

        try:
            sanitized_config = _sanitize_stored_user_config(stored_config)
            sanitized_config = _enrich_public_custom_mailbox_capabilities(
                stored_config,
                sanitized_config,
                session_user["email"],
            )
        except Exception:
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return
        if not isinstance(sanitized_config, dict):
            _send_json(
                self,
                503,
                _build_error("config_invalid", "User configuration is invalid."),
            )
            return

        _send_json(
            self,
            200,
            {"ok": True, "configState": "found", "config": sanitized_config},
        )

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

        if (
            not isinstance(session_user, dict)
            or session_user.get("userType") != "member"
        ):
            _send_json(
                self,
                401,
                _build_error(
                    "unauthorized",
                    "A valid member session is required.",
                ),
            )
            return

        payload, error_status, error = _read_json_body(self)
        if error:
            _send_json(self, error_status or 400, error)
            return
        is_completion_request = payload == {"operation": "complete_onboarding"}
        if (
            isinstance(payload, dict)
            and "operation" in payload
            and not is_completion_request
        ):
            _send_json(
                self,
                400,
                _build_error(
                    "invalid_request",
                    "Onboarding completion request is invalid.",
                ),
            )
            return
        if _contains_server_only_credential_field(payload):
            _send_json(
                self,
                400,
                _build_error(
                    "forbidden_server_field",
                    "Server-owned credential generation must not be supplied.",
                ),
            )
            return

        sanitized_config = None
        if not is_completion_request:
            try:
                sanitized_config = _sanitize_user_config(
                    payload or {},
                    session_user["email"],
                )
            except _OnboardingSessionValidationError:
                _send_json(
                    self,
                    400,
                    _build_error(
                        "invalid_onboarding_session",
                        "Onboarding session is invalid.",
                    ),
                )
                return

        store, _ = resolve_user_config_store()
        if not store:
            _send_json(
                self,
                503,
                _build_error("user_config_store_unavailable", "User config storage is not configured."),
            )
            return

        for _attempt in range(MAX_USER_CONFIG_CAS_ATTEMPTS):
            try:
                read_result = read_user_config_record(store, session_user["email"])
            except Exception:
                _send_json(
                    self,
                    503,
                    _build_error(
                        "user_config_store_unavailable",
                        "User config storage is temporarily unavailable.",
                    ),
                )
                return

            read_status = (
                read_result.get("status") if isinstance(read_result, dict) else None
            )
            stored_config = (
                read_result.get("config") if isinstance(read_result, dict) else None
            )
            if read_status == "ok":
                if not isinstance(stored_config, dict):
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "config_invalid",
                            "User configuration is invalid.",
                        ),
                    )
                    return
                existing_record = stored_config
                if (
                    not _has_valid_known_stored_config_shapes(existing_record)
                    or not _stored_config_owner_matches(
                        existing_record,
                        session_user["email"],
                    )
                ):
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "config_invalid",
                            "User configuration is invalid.",
                        ),
                    )
                    return
            elif read_status == "missing" and stored_config is None:
                existing_record = None
            elif read_status == "missing":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "config_invalid",
                        "User configuration is invalid.",
                    ),
                )
                return
            elif read_status == "unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "user_config_store_unavailable",
                        "User config storage is temporarily unavailable.",
                    ),
                )
                return
            else:
                _send_json(
                    self,
                    503,
                    _build_error(
                        "config_invalid",
                        "User configuration is invalid.",
                    ),
                )
                return

            if is_completion_request and existing_record is None:
                _send_json(
                    self,
                    409,
                    _build_error(
                        "onboarding_mailboxes_incomplete",
                        "One or more selected inboxes are not fully connected.",
                    ),
                )
                return

            try:
                merged_config = (
                    _build_authoritative_onboarding_completion(
                        existing_record,
                        session_user["email"],
                    )
                    if is_completion_request
                    else _merge_user_config(
                        existing_record,
                        sanitized_config,
                    )
                )
            except _OnboardingCompletionError as completion_error:
                _send_json(
                    self,
                    completion_error.status_code,
                    _build_error(
                        completion_error.code,
                        completion_error.message,
                    ),
                )
                return
            except _StoredUserConfigValidationError:
                _send_json(
                    self,
                    503,
                    _build_error(
                        "config_invalid",
                        "User configuration is invalid.",
                    ),
                )
                return
            except _OnboardingSessionValidationError:
                _send_json(
                    self,
                    400,
                    _build_error(
                        "invalid_onboarding_session",
                        "Onboarding session is invalid.",
                    ),
                )
                return

            if is_completion_request and _json_values_are_type_exact(
                merged_config,
                existing_record,
            ):
                try:
                    response_config = _sanitize_stored_user_config(existing_record)
                except Exception:
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "config_invalid",
                            "User configuration is invalid.",
                        ),
                    )
                    return
                _send_json(self, 200, {"ok": True, "config": response_config})
                return

            try:
                write_result = (
                    write_user_config_record_if_unchanged(
                        store,
                        session_user["email"],
                        existing_record,
                        merged_config,
                    )
                    if existing_record is not None
                    else write_user_config_record_if_missing(
                        store,
                        session_user["email"],
                        merged_config,
                    )
                )
            except Exception:
                write_result = None

            write_status = (
                write_result.get("status")
                if isinstance(write_result, dict)
                else None
            )
            if write_status in {"conflict", "missing"}:
                continue

            try:
                readback_result = read_user_config_record(
                    store,
                    session_user["email"],
                )
            except Exception:
                readback_result = None
            readback_status = (
                readback_result.get("status")
                if isinstance(readback_result, dict)
                else None
            )
            readback_config = (
                readback_result.get("config")
                if isinstance(readback_result, dict)
                else None
            )

            if (
                readback_status == "ok"
                and isinstance(readback_config, dict)
                and _json_values_are_type_exact(readback_config, merged_config)
            ):
                try:
                    response_config = _sanitize_stored_user_config(readback_config)
                except Exception:
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "config_invalid",
                            "User configuration is invalid.",
                        ),
                    )
                    return
                _send_json(self, 200, {"ok": True, "config": response_config})
                return

            if write_status != "ok":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "user_config_store_unavailable",
                        "User config storage is temporarily unavailable.",
                    ),
                )
                return

            if readback_status == "unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "user_config_store_unavailable",
                        "User config storage is temporarily unavailable.",
                    ),
                )
                return
            if readback_status == "ok" and isinstance(readback_config, dict):
                if (
                    not _has_valid_known_stored_config_shapes(readback_config)
                    or not _stored_config_owner_matches(
                        readback_config,
                        session_user["email"],
                    )
                ):
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "config_invalid",
                            "User configuration is invalid.",
                        ),
                    )
                    return
                continue
            if readback_status == "missing" and readback_config is None:
                continue

            _send_json(
                self,
                503,
                _build_error(
                    "config_invalid",
                    "User configuration is invalid.",
                ),
            )
            return

        _send_json(
            self,
            409,
            _build_error(
                "user_config_write_conflict",
                "User configuration changed concurrently. Please retry.",
            ),
        )

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
