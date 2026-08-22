import json
import re
import sys
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from authenticated_imap import resolve_authenticated_imap_mailbox  # noqa: E402
from imap_network_policy import (  # noqa: E402
    ImapNetworkPolicyError,
    normalize_imap_host,
)
from mailbox_secret_store import (  # noqa: E402
    generate_mailbox_credential_version,
    is_valid_mailbox_credential_version,
    read_mailbox_secret,
    restore_encrypted_mailbox_secret_snapshot,
    save_mailbox_secret,
    snapshot_mailbox_secret_namespace,
    snapshot_encrypted_mailbox_secret,
)
from user_config_store import (  # noqa: E402
    acquire_mailbox_mutation_lease,
    release_mailbox_mutation_lease,
    resolve_owned_initial_imap_registration,
    resolve_owned_onboarding_custom_imap_target,
    resolve_owned_onboarding_imap_registration,
    resolve_authenticated_user,
    resolve_owned_managed_inbox_record,
    rollback_owned_custom_imap_mailbox_update,
    upsert_owned_custom_imap_mailbox,
)
from api.priority.semantic_config import read_new_inbound_client_mode  # noqa: E402

INITIAL_FIELDS = {
    "mode",
    "mailboxId",
    "connection",
    "limit",
    "internalRole",
    "focusPreferences",
}
REFRESH_FIELDS = {"mode", "mailboxId", "limit", "focusPreferences"}
CONNECTION_FIELDS = {"provider", "email", "imap", "smtp"}
IMAP_FIELDS = {"host", "port", "ssl", "username", "password"}
IMAP_CONFIG_FIELDS = {"host", "port", "ssl", "username"}
SMTP_FIELDS = {
    "host",
    "port",
    "security",
    "username",
    "password",
    "useSameCredentials",
}
SMTP_CONFIG_FIELDS = {
    "host",
    "port",
    "security",
    "username",
    "useSameCredentials",
}
PASSWORD_PLACEHOLDERS = {
    "••••••••",
    "********",
    "stored securely",
    "stored securely — leave blank to reuse",
}
ONBOARDING_FIELDS = {
    "mode",
    "onboardingInboxId",
    "serverMailboxId",
    "connection",
}
ONBOARDING_CONNECTION_FIELDS = {"provider", "email", "imap", "smtp"}
ONBOARDING_IMAP_FIELDS = {"host", "port", "ssl", "username", "password"}
FORBIDDEN_CLIENT_AUTHORITY_FIELDS = {
    "id",
    "mailboxId",
    "managedInboxId",
    "serverMailboxId",
    "credentialId",
    "userId",
    "workspaceId",
    "ownerId",
    "ownerEmail",
    "oauthOwnerEmail",
    "credentialVersion",
    "secretVersion",
    "credentialGeneration",
    "secretGeneration",
}
IMAP_NETWORK_ERROR_CODES = {
    "imap_host_invalid",
    "imap_destination_not_allowed",
    "imap_dns_failed",
    "imap_peer_mismatch",
    "imap_connection_failed",
}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _safe_imap_network_error(code: str) -> tuple[int, dict] | None:
    if not isinstance(code, str) or code not in IMAP_NETWORK_ERROR_CODES:
        return None
    status_code = (
        400
        if code in {"imap_host_invalid", "imap_destination_not_allowed"}
        else 502
    )
    messages = {
        "imap_host_invalid": "The IMAP host is invalid.",
        "imap_destination_not_allowed": "The IMAP destination is not allowed.",
        "imap_dns_failed": "The IMAP destination could not be resolved.",
        "imap_peer_mismatch": "The IMAP destination could not be verified.",
        "imap_connection_failed": "A secure IMAP connection could not be established.",
    }
    return status_code, _error(code, messages[code])


def _valid_mailbox_id(value) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value.startswith("draft-")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
    ):
        return None
    return value


def _exact_string(value, *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    if not allow_empty and not value:
        return None
    if "\r" in value or "\n" in value:
        return None
    return value


def _normalized_email(value) -> str | None:
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        return None
    normalized = value.strip().lower()
    if not normalized or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        return None
    return normalized


def _credential_string(value) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        return None
    return value


def _port(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 65535 else None


def _has_only_fields(value, allowed: set[str]) -> bool:
    return isinstance(value, dict) and set(value).issubset(allowed)


def _is_password_placeholder(value) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    return (
        normalized in PASSWORD_PLACEHOLDERS
        or re.fullmatch(r"[*•●]{6,}", normalized) is not None
    )


def _parse_request_password(
    value,
    *,
    allow_missing: bool,
) -> str | None:
    if value is None or (
        isinstance(value, str) and not value.strip()
    ):
        return None if allow_missing else ""
    if _is_password_placeholder(value):
        return ""
    return _credential_string(value) or ""


def _stored_password_is_usable(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not _is_password_placeholder(value)
    )


def _contains_forbidden_client_authority(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                compact = "".join(
                    character
                    for character in key.strip().lower()
                    if character.isalnum()
                )
                is_generation = (
                    ("credential" in compact or "secret" in compact)
                    and (
                        "version" in compact
                        or "generation" in compact
                        or "revision" in compact
                    )
                )
                if key in FORBIDDEN_CLIENT_AUTHORITY_FIELDS or is_generation:
                    return True
            if _contains_forbidden_client_authority(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_client_authority(item) for item in value)
    return False


def _contains_forbidden_client_generation(value) -> bool:
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
            if _contains_forbidden_client_generation(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_client_generation(item) for item in value)
    return False


def _resolved_config_contains_mailbox_id(result: dict, mailbox_id: str) -> bool:
    config = result.get("config")
    managed_inboxes = config.get("managedInboxes") if isinstance(config, dict) else None
    return isinstance(managed_inboxes, list) and any(
        isinstance(inbox, dict) and inbox.get("id") == mailbox_id
        for inbox in managed_inboxes
    )


def _parse_smtp_connection(
    value,
    *,
    imap_username: str,
) -> tuple[dict | None, dict | None]:
    if not _has_only_fields(value, SMTP_FIELDS):
        return None, _error(
            "invalid_request",
            "Mailbox SMTP settings must be complete or absent.",
        )
    if not {
        "host",
        "port",
        "security",
        "useSameCredentials",
    }.issubset(value):
        return None, _error(
            "invalid_request",
            "Mailbox SMTP settings must be complete or absent.",
        )

    smtp_host = _exact_string(value.get("host"))
    smtp_port = _port(value.get("port"))
    smtp_security = value.get("security")
    use_same_credentials = value.get("useSameCredentials")
    if not isinstance(use_same_credentials, bool):
        return None, _error(
            "invalid_request",
            "Mailbox SMTP settings must be complete or absent.",
        )
    if not smtp_host or not smtp_port or smtp_security not in {"ssl", "starttls"}:
        return None, _error(
            "invalid_request",
            "Mailbox SMTP settings must be complete or absent.",
        )
    if (
        (smtp_security == "ssl" and smtp_port != 465)
        or (smtp_security == "starttls" and smtp_port != 587)
    ):
        return None, _error(
            "smtp_transport_not_allowed",
            "SMTP requires port 465 with SSL/TLS or port 587 with STARTTLS.",
        )

    supplied_password = value.get("password")
    if _is_password_placeholder(supplied_password):
        return None, _error(
            "invalid_request",
            "Mailbox SMTP settings must not contain a password placeholder.",
        )
    if use_same_credentials:
        if supplied_password not in {None, ""}:
            return None, _error(
                "invalid_request",
                "SMTP must omit a separate password when credentials are shared.",
            )
        supplied_username = value.get("username")
        if supplied_username is not None and _exact_string(
            supplied_username,
            allow_empty=True,
        ) is None:
            return None, _error(
                "invalid_request",
                "Mailbox SMTP settings must be complete or absent.",
            )
        smtp_username = ""
        smtp_password = None
        authentication_username = imap_username
    else:
        smtp_username = _exact_string(value.get("username"))
        smtp_password = _parse_request_password(
            supplied_password,
            allow_missing=False,
        )
        authentication_username = smtp_username
        if not smtp_username or not smtp_password:
            return None, _error(
                "invalid_request",
                "Explicit SMTP username and password are required.",
            )

    return {
        "host": smtp_host,
        "port": smtp_port,
        "security": smtp_security,
        "username": smtp_username,
        "authenticationUsername": authentication_username,
        "password": smtp_password,
        "useSameCredentials": use_same_credentials,
    }, None


def _parse_credential_connection(payload: dict) -> tuple[dict | None, dict | None]:
    if not _has_only_fields(payload, INITIAL_FIELDS):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")
    mode = payload.get("mode")
    mailbox_id = _valid_mailbox_id(payload.get("mailboxId"))
    connection = payload.get("connection")
    if (
        mode not in {"initial", "reconnect"}
        or not mailbox_id
        or not _has_only_fields(connection, CONNECTION_FIELDS)
    ):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")
    imap = connection.get("imap")
    if not _has_only_fields(imap, IMAP_FIELDS):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")

    email = _exact_string(connection.get("email"))
    imap_host = imap.get("host") if isinstance(imap.get("host"), str) else None
    imap_port = _port(imap.get("port"))
    imap_username = _exact_string(imap.get("username"))
    imap_password = _parse_request_password(
        imap.get("password"),
        allow_missing=mode == "reconnect",
    )
    if imap.get("ssl") is not True:
        return None, _error(
            "tls_required",
            "Custom IMAP connections require verified TLS.",
        )
    if (
        connection.get("provider") != "custom_imap"
        or not email
        or "@" not in email
        or imap_host is None
        or not imap_port
        or not imap_username
        or (mode == "initial" and not imap_password)
        or imap_password == ""
    ):
        return None, _error(
            "invalid_request",
            "Complete IMAP connection details are required.",
        )

    parsed_smtp = None
    if "smtp" in connection:
        parsed_smtp, smtp_error = _parse_smtp_connection(
            connection.get("smtp"),
            imap_username=imap_username,
        )
        if smtp_error:
            return None, smtp_error

    limit = payload.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
        return None, _error("invalid_request", "Connection limit is invalid.")

    return {
        "mode": mode,
        "mailboxId": mailbox_id,
        "email": email,
        "imap": {
            "host": imap_host,
            "port": imap_port,
            "ssl": imap["ssl"],
            "username": imap_username,
            "password": imap_password,
        },
        "smtp": parsed_smtp,
        "limit": limit,
        "internalRole": payload.get("internalRole"),
        "focusPreferences": payload.get("focusPreferences"),
    }, None


def _parse_onboarding_connection(payload: dict) -> tuple[dict | None, dict | None]:
    authority_checked_payload = (
        {key: value for key, value in payload.items() if key != "serverMailboxId"}
        if isinstance(payload, dict)
        else payload
    )
    if _contains_forbidden_client_authority(authority_checked_payload):
        return None, _error(
            "forbidden_client_authority",
            "Server-owned mailbox authority must not be supplied.",
        )
    if (
        not isinstance(payload, dict)
        or not {"mode", "onboardingInboxId", "connection"}.issubset(payload)
        or not set(payload).issubset(ONBOARDING_FIELDS)
    ):
        return None, _error("invalid_request", "Onboarding connection request is invalid.")

    onboarding_inbox_id = _exact_string(payload.get("onboardingInboxId"))
    server_mailbox_id = (
        _valid_mailbox_id(payload.get("serverMailboxId"))
        if "serverMailboxId" in payload
        else None
    )
    connection = payload.get("connection")
    if (
        payload.get("mode") != "onboarding"
        or not onboarding_inbox_id
        or (
            "serverMailboxId" in payload
            and server_mailbox_id is None
        )
        or not isinstance(connection, dict)
        or not {"provider", "email"}.issubset(connection)
        or not set(connection).issubset(ONBOARDING_CONNECTION_FIELDS)
    ):
        return None, _error("invalid_request", "Onboarding connection request is invalid.")

    imap = connection.get("imap")
    email = _normalized_email(connection.get("email"))
    if (
        connection.get("provider") != "custom_imap"
        or not email
    ):
        return None, _error("invalid_request", "Complete IMAP connection details are required.")

    parsed_imap = None
    imap_username = ""
    if imap is None:
        if server_mailbox_id is None:
            return None, _error(
                "invalid_request",
                "A new mailbox requires complete IMAP connection details.",
            )
    else:
        if (
            not isinstance(imap, dict)
            or not {"host", "port", "ssl", "username"}.issubset(imap)
            or not set(imap).issubset(ONBOARDING_IMAP_FIELDS)
        ):
            return None, _error(
                "invalid_request",
                "Onboarding connection request is invalid.",
            )
        host = imap.get("host") if isinstance(imap.get("host"), str) else None
        port = _port(imap.get("port"))
        imap_username = _exact_string(imap.get("username"))
        supplied_imap_password = imap.get("password")
        if _is_password_placeholder(supplied_imap_password):
            return None, _error(
                "invalid_request",
                "Mailbox passwords must not contain placeholders.",
            )
        password = _parse_request_password(
            supplied_imap_password,
            allow_missing=True,
        )
        if imap.get("ssl") is not True:
            return None, _error(
                "tls_required",
                "Custom IMAP onboarding requires verified TLS.",
            )
        if (
            host is None
            or not port
            or not imap_username
            or password == ""
        ):
            return None, _error(
                "invalid_request",
                "Complete IMAP connection details are required.",
            )
        parsed_imap = {
            "host": host,
            "port": port,
            "ssl": True,
            "username": imap_username,
            "password": password,
        }

    parsed_smtp = None
    if "smtp" in connection:
        parsed_smtp, smtp_error = _parse_smtp_connection(
            connection.get("smtp"),
            imap_username=imap_username,
        )
        if smtp_error:
            return None, smtp_error

    return {
        "onboardingInboxId": onboarding_inbox_id,
        "serverMailboxId": server_mailbox_id,
        "email": email,
        "imap": parsed_imap,
        "smtp": parsed_smtp,
    }, None


def _prepare_server_mailbox_id(
    config: dict,
    owner_email: str,
) -> tuple[str | None, dict | None, str | None, dict | None]:
    managed_inboxes = config.get("managedInboxes") if isinstance(config, dict) else None
    if not isinstance(managed_inboxes, list):
        return None, None, None, _error(
            "mailbox_configuration_malformed",
            "Mailbox configuration is invalid.",
        )
    existing_ids = {
        inbox["id"].casefold()
        for inbox in managed_inboxes
        if isinstance(inbox, dict) and isinstance(inbox.get("id"), str)
    }
    for _ in range(16):
        candidate = f"imap-{uuid.uuid4().hex}"
        if not _valid_mailbox_id(candidate) or candidate.casefold() in existing_ids:
            continue
        try:
            mailbox_lease = acquire_mailbox_mutation_lease(
                owner_email,
                candidate,
            )
        except Exception:
            mailbox_lease = {"status": "unavailable", "token": None, "error": None}
        if mailbox_lease.get("status") == "held":
            existing_ids.add(candidate.casefold())
            continue
        mailbox_lease_token = mailbox_lease.get("token")
        if mailbox_lease.get("status") != "acquired" or not isinstance(
            mailbox_lease_token,
            str,
        ):
            return None, None, None, _error(
                "mailbox_mutation_lease_unavailable",
                "Mailbox registration could not reserve its server identity.",
            )
        try:
            namespace_snapshot = snapshot_mailbox_secret_namespace(
                owner_email,
                candidate,
            )
        except Exception:
            namespace_snapshot = {
                "status": "unavailable",
                "record": None,
                "error": None,
            }
        if namespace_snapshot.get("status") == "missing":
            return (
                candidate,
                {"status": "missing", "record": None, "error": None},
                mailbox_lease_token,
                None,
            )
        try:
            release_mailbox_mutation_lease(
                owner_email,
                candidate,
                mailbox_lease_token,
            )
        except Exception:
            pass
        if namespace_snapshot.get("status") == "present":
            existing_ids.add(candidate.casefold())
            continue
        return None, None, None, _error(
            "mailbox_secret_store_unavailable",
            "Mailbox credential state could not be prepared.",
        )
    return None, None, None, _error(
        "mailbox_id_generation_failed",
        "Mailbox registration could not be prepared.",
    )


def _build_expected_onboarding_mailbox(
    parsed: dict,
    mailbox_id: str,
    credential_version: str,
) -> dict:
    expected = {
        "email": parsed["email"],
        "onboardingInboxId": parsed["onboardingInboxId"],
        "credentialVersion": credential_version,
        "customImap": {
            "host": parsed["imap"]["host"],
            "port": str(parsed["imap"]["port"]),
            "ssl": True,
            "username": parsed["imap"]["username"],
        },
        "customSmtp": {},
        "id": mailbox_id,
        "title": parsed["email"],
        "provider": "custom_imap",
        "connected": True,
        "connectionMethod": "imap",
        "connectionStatus": "connected",
        "connectionMessage": None,
        "oauthAuthorizationUrl": None,
    }
    parsed_smtp = parsed.get("smtp")
    if isinstance(parsed_smtp, dict):
        expected["customSmtp"] = {
            "host": parsed_smtp["host"],
            "port": str(parsed_smtp["port"]),
            "security": parsed_smtp["security"],
            "username": parsed_smtp["username"],
            "useSameCredentials": parsed_smtp["useSameCredentials"],
        }
        expected["imapConnectionStatus"] = "connected"
        expected["smtpConnectionStatus"] = "connected"
        expected["fullyConnected"] = True
    return expected


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


def _onboarding_readback_is_exact(
    result: dict,
    parsed: dict,
    mailbox_id: str,
    credential_version: str,
    baseline_config: dict,
) -> bool:
    if result.get("status") != "ok" or not isinstance(result.get("inbox"), dict):
        return False
    config = result.get("config")
    inbox = result["inbox"]
    expected_mailbox = _build_expected_onboarding_mailbox(
        parsed,
        mailbox_id,
        credential_version,
    )
    if (
        not isinstance(config, dict)
        or not _json_values_are_type_exact(
            config.get("onboardingSession"),
            baseline_config.get("onboardingSession"),
        )
        or not _json_values_are_type_exact(inbox, expected_mailbox)
    ):
        return False

    managed_inboxes = config.get("managedInboxes")
    if not isinstance(managed_inboxes, list):
        return False
    id_matches = [
        item
        for item in managed_inboxes
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].casefold() == mailbox_id.casefold()
    ]
    position_matches = [
        item
        for item in managed_inboxes
        if isinstance(item, dict)
        and item.get("onboardingInboxId") == parsed["onboardingInboxId"]
    ]
    email_matches = [
        item
        for item in managed_inboxes
        if isinstance(item, dict)
        and isinstance(item.get("email"), str)
        and item["email"].strip().lower() == parsed["email"]
    ]
    return (
        len(id_matches) == len(position_matches) == len(email_matches) == 1
        and _json_values_are_type_exact(id_matches[0], expected_mailbox)
        and _json_values_are_type_exact(position_matches[0], expected_mailbox)
        and _json_values_are_type_exact(email_matches[0], expected_mailbox)
    )


def _mailbox_readback_is_exact(
    result: dict,
    expected_inbox: dict,
    expected_onboarding_session: dict | None = None,
) -> bool:
    if (
        result.get("status") != "ok"
        or not isinstance(result.get("inbox"), dict)
        or not _json_values_are_type_exact(result["inbox"], expected_inbox)
    ):
        return False
    config = result.get("config")
    managed_inboxes = config.get("managedInboxes") if isinstance(config, dict) else None
    if (
        not isinstance(managed_inboxes, list)
        or (
            expected_onboarding_session is not None
            and not _json_values_are_type_exact(
                config.get("onboardingSession"),
                expected_onboarding_session,
            )
        )
    ):
        return False
    matches = [
        inbox
        for inbox in managed_inboxes
        if isinstance(inbox, dict)
        and inbox.get("id") == expected_inbox.get("id")
    ]
    return len(matches) == 1 and _json_values_are_type_exact(
        matches[0],
        expected_inbox,
    )


def _secret_readback_is_exact(
    result: dict,
    credential_version,
    imap_password,
    smtp_password,
) -> bool:
    record = result.get("record") if isinstance(result, dict) else None
    return (
        is_valid_mailbox_credential_version(credential_version)
        and isinstance(imap_password, str)
        and bool(imap_password)
        and isinstance(smtp_password, str)
        and result.get("status") == "present"
        and isinstance(record, dict)
        and record.get("credentialVersion") == credential_version
        and record.get("imapPassword") == imap_password
        and record.get("smtpPassword") == smtp_password
    )


def _reconnect_generation_state_is_valid(
    inbox: dict,
    secret_result: dict,
) -> bool:
    if secret_result.get("status") == "missing":
        secret_record = None
    elif secret_result.get("status") == "present" and isinstance(
        secret_result.get("record"), dict
    ):
        secret_record = secret_result["record"]
    else:
        return False

    config_has_generation = "credentialVersion" in inbox
    secret_has_generation = (
        isinstance(secret_record, dict) and "credentialVersion" in secret_record
    )
    if not config_has_generation and not secret_has_generation:
        return True
    if not config_has_generation or not secret_has_generation:
        return False
    config_generation = inbox.get("credentialVersion")
    secret_generation = secret_record.get("credentialVersion")
    return (
        is_valid_mailbox_credential_version(config_generation)
        and is_valid_mailbox_credential_version(secret_generation)
        and config_generation == secret_generation
    )


def _resolve_preserved_smtp_state(
    inbox: dict,
    secret_record: dict,
) -> tuple[dict | None, str | None]:
    custom_smtp = inbox.get("customSmtp")
    smtp_password = secret_record.get("smtpPassword")
    if not isinstance(custom_smtp, dict) or not isinstance(smtp_password, str):
        return None, None
    if not custom_smtp:
        return ({}, "") if smtp_password == "" else (None, None)
    if set(custom_smtp) != SMTP_CONFIG_FIELDS:
        return None, None

    host = _exact_string(custom_smtp.get("host"))
    port = _port(custom_smtp.get("port"))
    security = custom_smtp.get("security")
    username = _exact_string(custom_smtp.get("username"))
    use_same_credentials = custom_smtp.get("useSameCredentials")
    imap_password = secret_record.get("imapPassword")
    if (
        not host
        or not port
        or (security == "ssl" and port != 465)
        or (security == "starttls" and port != 587)
        or security not in {"ssl", "starttls"}
        or (not use_same_credentials and not username)
        or not isinstance(use_same_credentials, bool)
        or (
            use_same_credentials
            and not _stored_password_is_usable(imap_password)
        )
        or (
            not use_same_credentials
            and not _stored_password_is_usable(smtp_password)
        )
    ):
        return None, None
    return deepcopy(custom_smtp), smtp_password


def _build_expected_credential_mailbox(
    parsed: dict,
    payload: dict,
    existing_inbox: dict | None,
    credential_version: str,
) -> dict:
    existing = deepcopy(existing_inbox) if isinstance(existing_inbox, dict) else {}
    parsed_smtp = parsed.get("smtp")
    if isinstance(parsed_smtp, dict):
        custom_smtp = {
            "host": parsed_smtp["host"],
            "port": str(parsed_smtp["port"]),
            "security": parsed_smtp["security"],
            "username": parsed_smtp["username"],
            "useSameCredentials": parsed_smtp["useSameCredentials"],
        }
    else:
        existing_smtp = existing.get("customSmtp")
        custom_smtp = (
            deepcopy(existing_smtp)
            if isinstance(existing_smtp, dict)
            else {}
        )
    next_inbox = {
        **existing,
        "email": parsed["email"],
        "customImap": {
            "host": parsed["imap"]["host"],
            "port": str(parsed["imap"]["port"]),
            "ssl": parsed["imap"]["ssl"],
            "username": parsed["imap"]["username"],
        },
        "customSmtp": custom_smtp,
        "credentialVersion": credential_version,
        "id": parsed["mailboxId"],
        "title": existing.get("title") or parsed["email"] or "Custom Inbox",
        "provider": "custom_imap",
        "connected": True,
        "connectionMethod": "imap",
        "connectionStatus": "connected",
        "connectionMessage": None,
        "oauthAuthorizationUrl": None,
    }
    if isinstance(parsed_smtp, dict):
        next_inbox["imapConnectionStatus"] = "connected"
        next_inbox["smtpConnectionStatus"] = "connected"
        next_inbox["fullyConnected"] = True
    for field in ("internalRole", "focusPreferences"):
        if field in payload:
            next_inbox[field] = deepcopy(payload[field])
    return next_inbox


def _preview_success_payload(payload: dict) -> dict:
    response = {"ok": True}
    for key in ("messages", "inboxUidSet", "uidValidity"):
        if key in payload:
            response[key] = payload[key]
    if isinstance(payload.get("warning"), dict):
        response["warning"] = {
            "code": payload["warning"].get("code"),
            "stage": payload["warning"].get("stage"),
            "message": "Some messages could not be fetched.",
            "fetched_count": payload["warning"].get("fetched_count"),
        }
    if isinstance(payload.get("warnings"), list):
        response["warnings"] = [
            {
                "code": warning.get("code"),
                "stage": warning.get("stage"),
                "message": "Some messages could not be fetched.",
                "fetched_count": warning.get("fetched_count"),
            }
            for warning in payload["warnings"]
            if isinstance(warning, dict)
        ]
    return response


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, payload: dict):
        response_body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_POST(self):
        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not session_user:
            if auth_error and auth_error.get("code") == "session_auth_unavailable":
                self._send_json(
                    503,
                    _error("session_auth_unavailable", "Authentication is temporarily unavailable."),
                )
            else:
                self._send_json(401, _error("unauthorized", "A valid member session is required."))
            return

        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self._send_json(400, _error("invalid_request", "Request body must be valid JSON."))
            return
        if not isinstance(payload, dict):
            self._send_json(400, _error("invalid_request", "Request body must be a JSON object."))
            return
        if _contains_forbidden_client_generation(payload):
            self._send_json(
                400,
                _error(
                    "forbidden_client_authority",
                    "Server-owned mailbox authority must not be supplied.",
                ),
            )
            return

        mode = payload.get("mode")
        if mode == "refresh":
            self._handle_refresh(payload)
            return
        if mode == "onboarding":
            self._handle_onboarding_connection(payload, session_user)
            return
        if mode in {"initial", "reconnect"}:
            self._handle_credential_connection(payload, session_user, mode)
            return
        self._send_json(400, _error("invalid_request", "Connection mode is required."))

    def _send_onboarding_registration_error(self, result: dict):
        code = (result.get("error") or {}).get("code")
        responses = {
            "onboarding_unavailable": (
                409,
                "onboarding_unavailable",
                "Onboarding is unavailable for mailbox registration.",
            ),
            "onboarding_completed": (
                409,
                "onboarding_completed",
                "Completed onboarding cannot register another mailbox.",
            ),
            "unknown_inbox_position": (
                400,
                "unknown_inbox_position",
                "Inbox position is invalid.",
            ),
            "inbox_position_not_selected": (
                409,
                "inbox_position_not_selected",
                "Inbox position is not selected in onboarding.",
            ),
            "inbox_position_conflict": (
                409,
                "inbox_position_conflict",
                "Inbox position is already connected.",
            ),
            "mailbox_email_conflict": (
                409,
                "mailbox_already_registered",
                "Mailbox is already registered.",
            ),
            "mailbox_id_conflict": (
                409,
                "mailbox_registration_conflict",
                "Mailbox registration conflicted with an existing mailbox.",
            ),
            "user_config_write_conflict": (
                409,
                "mailbox_registration_conflict",
                "Mailbox registration conflicted with a concurrent configuration update.",
            ),
        }
        status_code, response_code, message = responses.get(
            code,
            (
                503 if result.get("status") == "unavailable" else 500,
                "mailbox_configuration_unavailable"
                if result.get("status") == "unavailable"
                else "mailbox_configuration_malformed",
                "Mailbox configuration is temporarily unavailable."
                if result.get("status") == "unavailable"
                else "Mailbox configuration is invalid.",
            ),
        )
        self._send_json(status_code, _error(response_code, message))

    def _restore_onboarding_secret(
        self,
        session_user: dict,
        mailbox_id: str,
        previous_secret: dict,
        credential_version: str,
    ) -> dict | None:
        try:
            return restore_encrypted_mailbox_secret_snapshot(
                session_user["email"],
                mailbox_id,
                previous_secret,
                expected_credential_version=credential_version,
            )
        except Exception:
            return {
                "code": "mailbox_secret_store_unavailable",
                "message": "Mailbox connection state could not be restored safely.",
            }

    def _send_secret_write_error(self, error: dict | None):
        code = error.get("code") if isinstance(error, dict) else None
        if code == "mailbox_secret_write_conflict":
            self._send_json(
                409,
                _error(
                    "mailbox_connection_conflict",
                    "Mailbox credentials changed during this request.",
                ),
            )
            return
        if code == "mailbox_secret_write_ambiguous":
            self._send_json(
                503,
                _error(
                    "mailbox_secret_write_ambiguous",
                    "Mailbox credential persistence could not be verified safely.",
                ),
            )
            return
        self._send_json(
            503,
            _error(
                "mailbox_secret_store_unavailable",
                "Mailbox credentials could not be stored.",
            ),
        )

    def _send_rollback_error(self, error: dict | None):
        code = error.get("code") if isinstance(error, dict) else None
        if code in {
            "mailbox_secret_write_conflict",
            "user_config_newer_mailbox_preserved",
            "user_config_write_conflict",
        }:
            self._send_json(
                409,
                _error(
                    "mailbox_connection_conflict",
                    "A newer mailbox connection was preserved.",
                ),
            )
            return
        self._send_json(
            503,
            _error(
                "mailbox_connection_rollback_failed",
                "Mailbox connection state could not be restored safely.",
            ),
        )

    def _handle_onboarding_connection(self, payload: dict, session_user: dict):
        parsed, parse_error = _parse_onboarding_connection(payload)
        if parse_error or not parsed:
            self._send_json(400, parse_error or _error("invalid_request", "Request is invalid."))
            return

        mutation_lease_mailbox_id = (
            f"onboarding:{parsed['onboardingInboxId'].casefold()}"
        )
        try:
            mutation_lease = acquire_mailbox_mutation_lease(
                session_user["email"],
                mutation_lease_mailbox_id,
            )
        except Exception:
            mutation_lease = {"status": "unavailable", "token": None, "error": None}
        if mutation_lease.get("status") != "acquired" or not isinstance(
            mutation_lease.get("token"),
            str,
        ):
            lease_held = mutation_lease.get("status") == "held"
            self._send_json(
                409 if lease_held else 503,
                _error(
                    "mailbox_connection_in_progress"
                    if lease_held
                    else "mailbox_mutation_lease_unavailable",
                    "Another connection update is already in progress."
                    if lease_held
                    else "Mailbox connection state could not be reserved safely.",
                ),
            )
            return
        mutation_lease_token = mutation_lease["token"]
        try:
            self._handle_onboarding_connection_with_position_lease(
                parsed,
                session_user,
            )
        finally:
            try:
                release_mailbox_mutation_lease(
                    session_user["email"],
                    mutation_lease_mailbox_id,
                    mutation_lease_token,
                )
            except Exception:
                pass

    def _handle_onboarding_connection_with_position_lease(
        self,
        parsed: dict,
        session_user: dict,
    ):
        self._handle_onboarding_capability_connection_with_position_lease(
            parsed,
            session_user,
        )
        return

        try:
            target = resolve_owned_onboarding_imap_registration(
                self.headers,
                parsed["onboardingInboxId"],
                parsed["email"],
            )
        except Exception:
            target = {
                "status": "unavailable",
                "config": None,
                "error": None,
            }
        if (
            target.get("status") != "ok"
            or not isinstance(target.get("config"), dict)
            or not isinstance(target.get("user"), dict)
            or target["user"].get("email") != session_user.get("email")
        ):
            self._send_onboarding_registration_error(target)
            return

        if isinstance(parsed.get("imap"), dict):
            try:
                parsed["imap"]["host"] = normalize_imap_host(
                    parsed["imap"]["host"]
                )
            except ImapNetworkPolicyError:
                self._send_json(
                    400,
                    _error("imap_host_invalid", "The IMAP host is invalid."),
                )
                return
        if (
            isinstance(parsed.get("imap"), dict)
            and parsed["imap"]["port"] != 993
        ):
            self._send_json(
                400,
                _error(
                    "imap_port_not_allowed",
                    "Custom IMAP onboarding requires port 993.",
                ),
            )
            return

        (
            mailbox_id,
            previous_secret,
            mailbox_mutation_lease_token,
            mailbox_id_error,
        ) = _prepare_server_mailbox_id(
            target["config"],
            session_user["email"],
        )
        if (
            mailbox_id_error
            or not mailbox_id
            or not previous_secret
            or not mailbox_mutation_lease_token
        ):
            self._send_json(
                503,
                mailbox_id_error
                or _error(
                    "mailbox_id_generation_failed",
                    "Mailbox registration could not be prepared.",
                ),
            )
            return
        try:
            self._handle_onboarding_connection_under_lease(
                parsed,
                session_user,
                target,
                mailbox_id,
                previous_secret,
            )
        finally:
            try:
                release_mailbox_mutation_lease(
                    session_user["email"],
                    mailbox_id,
                    mailbox_mutation_lease_token,
                )
            except Exception:
                pass

    def _handle_onboarding_connection_under_lease(
        self,
        parsed: dict,
        session_user: dict,
        target: dict,
        mailbox_id: str,
        previous_secret: dict,
    ):
        secure_request = {
            "host": parsed["imap"]["host"],
            "port": parsed["imap"]["port"],
            "ssl": True,
            "username": parsed["imap"]["username"],
            "password": parsed["imap"]["password"],
        }
        try:
            from imap_connect_preview import build_secure_imap_authentication_response

            status_code, response_payload = build_secure_imap_authentication_response(
                secure_request
            )
        except Exception:
            status_code, response_payload = 502, {
                "ok": False,
                "error": {"code": "tls_connection_failed"},
            }
        if status_code >= 400 or response_payload.get("ok") is not True:
            error_code = (response_payload.get("error") or {}).get("code")
            if error_code == "authentication_failed":
                self._send_json(
                    502,
                    _error("authentication_failed", "Mailbox authentication failed."),
                )
            elif safe_error := _safe_imap_network_error(error_code):
                self._send_json(*safe_error)
            else:
                self._send_json(
                    502,
                    _error(
                        "tls_connection_failed",
                        "A verified TLS connection could not be established.",
                    ),
                )
            return

        credential_version = generate_mailbox_credential_version()
        if not is_valid_mailbox_credential_version(credential_version):
            self._send_json(
                503,
                _error(
                    "mailbox_secret_store_unavailable",
                    "Mailbox credential state could not be prepared.",
                ),
            )
            return
        expected_mailbox = _build_expected_onboarding_mailbox(
            parsed,
            mailbox_id,
            credential_version,
        )
        try:
            saved_secret, save_error = save_mailbox_secret(
                session_user["email"],
                mailbox_id,
                imap_password=parsed["imap"]["password"],
                smtp_password=None,
                credential_version=credential_version,
                expected_snapshot=previous_secret,
                require_namespace_missing=True,
            )
        except Exception:
            saved_secret, save_error = None, {
                "code": "mailbox_secret_store_unavailable"
            }
        if save_error or not saved_secret:
            self._send_secret_write_error(save_error)
            return

        try:
            upsert_result = upsert_owned_custom_imap_mailbox(
                self.headers,
                mailbox_id,
                "initial",
                {
                    "email": parsed["email"],
                    "onboardingInboxId": parsed["onboardingInboxId"],
                    "customImap": {
                        "host": parsed["imap"]["host"],
                        "port": str(parsed["imap"]["port"]),
                        "ssl": True,
                        "username": parsed["imap"]["username"],
                    },
                    "customSmtp": {},
                },
                credential_version=credential_version,
                expected_inbox=None,
                onboarding_inbox_id=parsed["onboardingInboxId"],
            )
        except Exception:
            upsert_result = {"status": "unavailable", "error": None}

        try:
            readback = resolve_owned_managed_inbox_record(self.headers, mailbox_id)
        except Exception:
            readback = {"status": "unavailable", "inbox": None, "config": None}
        try:
            secret_readback = read_mailbox_secret(
                session_user["email"],
                mailbox_id,
            )
        except Exception:
            secret_readback = {"status": "unavailable", "record": None, "error": None}
        committed_exactly = _onboarding_readback_is_exact(
            readback,
            parsed,
            mailbox_id,
            credential_version,
            target["config"],
        ) and _secret_readback_is_exact(
            secret_readback,
            credential_version,
            parsed["imap"]["password"],
            "",
        )
        if committed_exactly:
            self._send_json(200, {"ok": True})
            return

        try:
            config_rollback_error = rollback_owned_custom_imap_mailbox_update(
                self.headers,
                mailbox_id,
                expected_mailbox,
                None,
            )
        except Exception:
            config_rollback_error = {
                "code": "user_config_store_unavailable",
                "message": "Mailbox configuration could not be restored.",
            }
        if config_rollback_error:
            if (
                isinstance(config_rollback_error, dict)
                and config_rollback_error.get("code")
                == "user_config_newer_mailbox_preserved"
            ):
                secret_rollback_error = self._restore_onboarding_secret(
                    session_user,
                    mailbox_id,
                    previous_secret,
                    credential_version,
                )
                if (
                    secret_rollback_error
                    and (
                        not isinstance(secret_rollback_error, dict)
                        or secret_rollback_error.get("code")
                        != "mailbox_secret_write_conflict"
                    )
                ):
                    self._send_rollback_error(secret_rollback_error)
                    return
            self._send_rollback_error(config_rollback_error)
            return
        secret_rollback_error = self._restore_onboarding_secret(
            session_user,
            mailbox_id,
            previous_secret,
            credential_version,
        )
        if secret_rollback_error:
            self._send_rollback_error(secret_rollback_error)
            return

        if upsert_result.get("status") != "ok":
            self._send_onboarding_registration_error(upsert_result)
            return
        self._send_json(
            503,
            _error(
                "configuration_persistence_failed",
                "Mailbox configuration could not be verified.",
            ),
        )
        return

    def _handle_onboarding_capability_connection_with_position_lease(
        self,
        parsed: dict,
        session_user: dict,
    ):
        if not isinstance(parsed.get("smtp"), dict):
            self._send_json(
                400,
                _error(
                    "invalid_request",
                    "Complete SMTP connection details are required during onboarding.",
                ),
            )
            return

        if isinstance(parsed.get("imap"), dict):
            try:
                parsed["imap"]["host"] = normalize_imap_host(
                    parsed["imap"]["host"]
                )
            except ImapNetworkPolicyError:
                self._send_json(
                    400,
                    _error("imap_host_invalid", "The IMAP host is invalid."),
                )
                return
        try:
            parsed["smtp"]["host"] = normalize_imap_host(parsed["smtp"]["host"])
        except ImapNetworkPolicyError:
            self._send_json(
                400,
                _error("smtp_host_invalid", "The SMTP host is invalid."),
            )
            return
        if (
            isinstance(parsed.get("imap"), dict)
            and parsed["imap"]["port"] != 993
        ):
            self._send_json(
                400,
                _error(
                    "imap_port_not_allowed",
                    "Custom IMAP onboarding requires port 993.",
                ),
            )
            return

        try:
            target = resolve_owned_onboarding_custom_imap_target(
                self.headers,
                parsed["onboardingInboxId"],
                parsed["email"],
                parsed.get("serverMailboxId"),
            )
        except Exception:
            target = {
                "status": "unavailable",
                "user": None,
                "inbox": None,
                "config": None,
                "error": None,
            }
        if (
            target.get("status") != "ok"
            or not isinstance(target.get("config"), dict)
            or not isinstance(target.get("user"), dict)
            or target["user"].get("email") != session_user.get("email")
        ):
            self._send_onboarding_registration_error(target)
            return

        existing_inbox = target.get("inbox")
        if existing_inbox is None:
            if (
                not isinstance(parsed.get("imap"), dict)
                or not _stored_password_is_usable(
                    parsed["imap"].get("password")
                )
            ):
                self._send_json(
                    400,
                    _error(
                        "invalid_request",
                        "A new mailbox requires an IMAP password.",
                    ),
                )
                return
            (
                mailbox_id,
                previous_secret,
                mailbox_mutation_lease_token,
                mailbox_id_error,
            ) = _prepare_server_mailbox_id(
                target["config"],
                session_user["email"],
            )
            if (
                mailbox_id_error
                or not mailbox_id
                or not previous_secret
                or not mailbox_mutation_lease_token
            ):
                self._send_json(
                    503,
                    mailbox_id_error
                    or _error(
                        "mailbox_id_generation_failed",
                        "Mailbox registration could not be prepared.",
                    ),
                )
                return
            try:
                self._handle_onboarding_capability_connection_under_lease(
                    parsed,
                    session_user,
                    target,
                    mailbox_id,
                    previous_secret,
                    None,
                )
            finally:
                try:
                    release_mailbox_mutation_lease(
                        session_user["email"],
                        mailbox_id,
                        mailbox_mutation_lease_token,
                    )
                except Exception:
                    pass
            return

        if not isinstance(existing_inbox, dict):
            self._send_json(
                500,
                _error(
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                ),
            )
            return
        if parsed.get("imap") is None:
            custom_imap = existing_inbox.get("customImap")
            if (
                not isinstance(custom_imap, dict)
                or set(custom_imap) != IMAP_CONFIG_FIELDS
                or custom_imap.get("ssl") is not True
                or _port(custom_imap.get("port")) != 993
                or not _exact_string(custom_imap.get("username"))
            ):
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "The existing incoming connection could not be verified safely.",
                    ),
                )
                return
            try:
                authoritative_imap_host = normalize_imap_host(
                    custom_imap.get("host")
                )
            except ImapNetworkPolicyError:
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "The existing incoming connection could not be verified safely.",
                    ),
                )
                return
            parsed["imap"] = {
                "host": authoritative_imap_host,
                "port": 993,
                "ssl": True,
                "username": custom_imap["username"],
                "password": None,
            }
            if parsed["smtp"].get("useSameCredentials") is True:
                parsed["smtp"]["authenticationUsername"] = custom_imap[
                    "username"
                ]
        mailbox_id = _valid_mailbox_id(existing_inbox.get("id"))
        if not mailbox_id:
            self._send_json(
                500,
                _error(
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                ),
            )
            return
        if parsed["imap"].get("password") is not None:
            self._send_json(
                400,
                _error(
                    "invalid_request",
                    "An existing incoming connection must reuse its stored IMAP credential.",
                ),
            )
            return

        try:
            mailbox_lease = acquire_mailbox_mutation_lease(
                session_user["email"],
                mailbox_id,
            )
        except Exception:
            mailbox_lease = {"status": "unavailable", "token": None, "error": None}
        mailbox_lease_token = mailbox_lease.get("token")
        if mailbox_lease.get("status") != "acquired" or not isinstance(
            mailbox_lease_token,
            str,
        ):
            lease_held = mailbox_lease.get("status") == "held"
            self._send_json(
                409 if lease_held else 503,
                _error(
                    "mailbox_connection_in_progress"
                    if lease_held
                    else "mailbox_mutation_lease_unavailable",
                    "Another connection update is already in progress."
                    if lease_held
                    else "Mailbox connection state could not be reserved safely.",
                ),
            )
            return
        try:
            try:
                current_target = resolve_owned_onboarding_custom_imap_target(
                    self.headers,
                    parsed["onboardingInboxId"],
                    parsed["email"],
                    mailbox_id,
                )
            except Exception:
                current_target = {
                    "status": "unavailable",
                    "user": None,
                    "inbox": None,
                    "config": None,
                    "error": None,
                }
            if (
                current_target.get("status") != "ok"
                or not isinstance(current_target.get("inbox"), dict)
                or not _json_values_are_type_exact(
                    current_target["inbox"],
                    existing_inbox,
                )
            ):
                self._send_onboarding_registration_error(current_target)
                return
            try:
                previous_secret = snapshot_encrypted_mailbox_secret(
                    session_user["email"],
                    mailbox_id,
                )
            except Exception:
                previous_secret = {
                    "status": "unavailable",
                    "record": None,
                    "error": None,
                }
            self._handle_onboarding_capability_connection_under_lease(
                parsed,
                session_user,
                current_target,
                mailbox_id,
                previous_secret,
                current_target["inbox"],
            )
        finally:
            try:
                release_mailbox_mutation_lease(
                    session_user["email"],
                    mailbox_id,
                    mailbox_lease_token,
                )
            except Exception:
                pass

    def _handle_onboarding_capability_connection_under_lease(
        self,
        parsed: dict,
        session_user: dict,
        target: dict,
        mailbox_id: str,
        previous_secret: dict,
        existing_inbox: dict | None,
    ):
        is_existing = isinstance(existing_inbox, dict)
        expected_onboarding_session = (
            target.get("config", {}).get("onboardingSession")
            if isinstance(target.get("config"), dict)
            else None
        )
        if not isinstance(expected_onboarding_session, dict):
            self._send_json(
                409,
                _error(
                    "reconnect_required",
                    "The authoritative onboarding state could not be verified safely.",
                ),
            )
            return
        expected_onboarding_session = deepcopy(expected_onboarding_session)
        if is_existing:
            try:
                previous_secret_read = read_mailbox_secret(
                    session_user["email"],
                    mailbox_id,
                )
            except Exception:
                previous_secret_read = {
                    "status": "unavailable",
                    "record": None,
                    "error": None,
                }
            previous_secret_record = (
                previous_secret_read.get("record")
                if previous_secret_read.get("status") == "present"
                and isinstance(previous_secret_read.get("record"), dict)
                else None
            )
            custom_imap = existing_inbox.get("customImap")
            custom_smtp = existing_inbox.get("customSmtp")
            smtp_is_unconfigured = (
                isinstance(custom_smtp, dict)
                and (
                    not custom_smtp
                    or _json_values_are_type_exact(
                        custom_smtp,
                        {"password": ""},
                    )
                )
            )
            config_generation = existing_inbox.get("credentialVersion")
            existing_imap_password = (
                previous_secret_record.get("imapPassword")
                if isinstance(previous_secret_record, dict)
                else None
            )
            expected_imap = {
                "host": parsed["imap"]["host"],
                "port": str(parsed["imap"]["port"]),
                "ssl": True,
                "username": parsed["imap"]["username"],
            }
            if (
                previous_secret.get("status") != "present"
                or not _reconnect_generation_state_is_valid(
                    existing_inbox,
                    previous_secret_read,
                )
                or not is_valid_mailbox_credential_version(config_generation)
                or not _stored_password_is_usable(existing_imap_password)
                or existing_inbox.get("provider") != "custom_imap"
                or existing_inbox.get("onboardingInboxId")
                != parsed["onboardingInboxId"]
                or _normalized_email(existing_inbox.get("email")) != parsed["email"]
                or existing_inbox.get("connected") is not True
                or existing_inbox.get("connectionStatus") != "connected"
                or existing_inbox.get("imapConnectionStatus")
                not in {None, "connected"}
                or existing_inbox.get("smtpConnectionStatus")
                not in {None, "not_configured", "not_connected"}
                or existing_inbox.get("fullyConnected") not in {None, False}
                or not _json_values_are_type_exact(custom_imap, expected_imap)
                or not smtp_is_unconfigured
            ):
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "The existing incoming connection could not be verified safely.",
                    ),
                )
                return
            effective_imap_password = existing_imap_password
        else:
            previous_secret_record = None
            effective_imap_password = parsed["imap"]["password"]
            secure_request = {
                "host": parsed["imap"]["host"],
                "port": parsed["imap"]["port"],
                "ssl": True,
                "username": parsed["imap"]["username"],
                "password": effective_imap_password,
            }
            try:
                from imap_connect_preview import (
                    build_secure_imap_authentication_response,
                )

                status_code, response_payload = (
                    build_secure_imap_authentication_response(secure_request)
                )
            except Exception:
                status_code, response_payload = 502, {
                    "ok": False,
                    "error": {"code": "tls_connection_failed"},
                }
            if status_code >= 400 or response_payload.get("ok") is not True:
                error_code = (response_payload.get("error") or {}).get("code")
                if error_code == "authentication_failed":
                    self._send_json(
                        502,
                        _error(
                            "authentication_failed",
                            "Mailbox authentication failed.",
                        ),
                    )
                elif safe_error := _safe_imap_network_error(error_code):
                    self._send_json(*safe_error)
                else:
                    self._send_json(
                        502,
                        _error(
                            "tls_connection_failed",
                            "A verified TLS connection could not be established.",
                        ),
                    )
                return

        parsed_smtp = parsed["smtp"]
        smtp_authentication_password = (
            effective_imap_password
            if parsed_smtp["useSameCredentials"]
            else parsed_smtp["password"]
        )
        stored_smtp_password = (
            "" if parsed_smtp["useSameCredentials"] else parsed_smtp["password"]
        )
        try:
            from smtp_connection import test_smtp_authentication

            smtp_status, smtp_payload = test_smtp_authentication(
                {
                    "host": parsed_smtp["host"],
                    "port": parsed_smtp["port"],
                    "security": parsed_smtp["security"],
                    "username": parsed_smtp["authenticationUsername"],
                    "password": smtp_authentication_password,
                }
            )
        except Exception:
            smtp_status, smtp_payload = 502, {
                "ok": False,
                "error": {"code": "smtp_connection_failed"},
            }
        if smtp_status >= 400 or smtp_payload.get("ok") is not True:
            error_code = (smtp_payload.get("error") or {}).get("code")
            public_code = (
                error_code
                if error_code
                in {
                    "smtp_authentication_failed",
                    "smtp_connection_failed",
                    "smtp_destination_not_allowed",
                    "smtp_dns_failed",
                    "smtp_peer_mismatch",
                    "smtp_tls_failed",
                }
                else "smtp_connection_failed"
            )
            self._send_json(
                400
                if public_code == "smtp_destination_not_allowed"
                else 502,
                _error(
                    public_code,
                    "SMTP authentication failed."
                    if public_code == "smtp_authentication_failed"
                    else "A secure SMTP connection could not be established.",
                ),
            )
            return

        credential_version = generate_mailbox_credential_version()
        if not is_valid_mailbox_credential_version(credential_version):
            self._send_json(
                503,
                _error(
                    "mailbox_secret_store_unavailable",
                    "Mailbox credential state could not be prepared.",
                ),
            )
            return

        parsed_for_mailbox = {
            **parsed,
            "mailboxId": mailbox_id,
        }
        expected_mailbox = (
            _build_expected_credential_mailbox(
                parsed_for_mailbox,
                {},
                existing_inbox,
                credential_version,
            )
            if is_existing
            else _build_expected_onboarding_mailbox(
                parsed,
                mailbox_id,
                credential_version,
            )
        )
        try:
            saved_secret, save_error = save_mailbox_secret(
                session_user["email"],
                mailbox_id,
                imap_password=effective_imap_password,
                smtp_password=stored_smtp_password,
                credential_version=credential_version,
                expected_snapshot=previous_secret,
                require_namespace_missing=not is_existing,
            )
        except Exception:
            saved_secret, save_error = None, {
                "code": "mailbox_secret_store_unavailable"
            }
        if save_error or not isinstance(saved_secret, dict):
            self._send_secret_write_error(save_error)
            return

        connection_metadata = {
            "email": parsed["email"],
            "customImap": {
                "host": parsed["imap"]["host"],
                "port": str(parsed["imap"]["port"]),
                "ssl": True,
                "username": parsed["imap"]["username"],
            },
            "customSmtp": deepcopy(expected_mailbox["customSmtp"]),
            "imapConnectionStatus": "connected",
            "smtpConnectionStatus": "connected",
            "fullyConnected": True,
        }
        if not is_existing:
            connection_metadata["onboardingInboxId"] = parsed[
                "onboardingInboxId"
            ]
        try:
            upsert_result = upsert_owned_custom_imap_mailbox(
                self.headers,
                mailbox_id,
                "reconnect" if is_existing else "initial",
                connection_metadata,
                credential_version=credential_version,
                expected_inbox=existing_inbox,
                onboarding_inbox_id=(
                    None if is_existing else parsed["onboardingInboxId"]
                ),
                expected_onboarding_session=expected_onboarding_session,
            )
        except Exception:
            upsert_result = {"status": "unavailable", "error": None}

        try:
            config_readback = resolve_owned_managed_inbox_record(
                self.headers,
                mailbox_id,
            )
        except Exception:
            config_readback = {
                "status": "unavailable",
                "inbox": None,
                "config": None,
            }
        try:
            secret_readback = read_mailbox_secret(
                session_user["email"],
                mailbox_id,
            )
        except Exception:
            secret_readback = {
                "status": "unavailable",
                "record": None,
                "error": None,
            }
        committed_exactly = _mailbox_readback_is_exact(
            config_readback,
            expected_mailbox,
            expected_onboarding_session,
        ) and _secret_readback_is_exact(
            secret_readback,
            credential_version,
            effective_imap_password,
            stored_smtp_password,
        )
        if committed_exactly:
            self._send_json(200, {"ok": True})
            return

        try:
            config_rollback_error = rollback_owned_custom_imap_mailbox_update(
                self.headers,
                mailbox_id,
                expected_mailbox,
                existing_inbox,
            )
        except Exception:
            config_rollback_error = {
                "code": "user_config_store_unavailable",
                "message": "Mailbox configuration could not be restored safely.",
            }
        if config_rollback_error:
            if (
                isinstance(config_rollback_error, dict)
                and config_rollback_error.get("code")
                == "user_config_newer_mailbox_preserved"
            ):
                try:
                    secret_rollback_error = (
                        restore_encrypted_mailbox_secret_snapshot(
                            session_user["email"],
                            mailbox_id,
                            previous_secret,
                            expected_credential_version=credential_version,
                        )
                    )
                except Exception:
                    secret_rollback_error = {
                        "code": "mailbox_secret_store_unavailable"
                    }
                if (
                    secret_rollback_error
                    and (
                        not isinstance(secret_rollback_error, dict)
                        or secret_rollback_error.get("code")
                        != "mailbox_secret_write_conflict"
                    )
                ):
                    self._send_rollback_error(secret_rollback_error)
                    return
            self._send_rollback_error(config_rollback_error)
            return
        try:
            secret_rollback_error = restore_encrypted_mailbox_secret_snapshot(
                session_user["email"],
                mailbox_id,
                previous_secret,
                expected_credential_version=credential_version,
            )
        except Exception:
            secret_rollback_error = {
                "code": "mailbox_secret_store_unavailable"
            }
        if secret_rollback_error:
            self._send_rollback_error(secret_rollback_error)
            return

        if upsert_result.get("status") != "ok":
            self._send_onboarding_registration_error(upsert_result)
            return
        self._send_json(
            503,
            _error(
                "configuration_persistence_failed",
                "Mailbox configuration could not be verified.",
            ),
        )

    def _handle_credential_connection(
        self,
        payload: dict,
        session_user: dict,
        mode: str,
    ):
        parsed, parse_error = _parse_credential_connection(payload)
        if parse_error or not parsed:
            self._send_json(400, parse_error or _error("invalid_request", "Request is invalid."))
            return

        try:
            mutation_lease = acquire_mailbox_mutation_lease(
                session_user["email"],
                parsed["mailboxId"],
            )
        except Exception:
            mutation_lease = {"status": "unavailable", "token": None, "error": None}
        if mutation_lease.get("status") != "acquired" or not isinstance(
            mutation_lease.get("token"),
            str,
        ):
            lease_held = mutation_lease.get("status") == "held"
            self._send_json(
                409 if lease_held else 503,
                _error(
                    "mailbox_connection_in_progress"
                    if lease_held
                    else "mailbox_mutation_lease_unavailable",
                    "Another connection update is already in progress."
                    if lease_held
                    else "Mailbox connection state could not be reserved safely.",
                ),
            )
            return
        mutation_lease_token = mutation_lease["token"]
        try:
            self._handle_credential_connection_under_lease(
                payload,
                session_user,
                mode,
                parsed,
            )
        finally:
            try:
                release_mailbox_mutation_lease(
                    session_user["email"],
                    parsed["mailboxId"],
                    mutation_lease_token,
                )
            except Exception:
                pass

    def _handle_credential_connection_under_lease(
        self,
        payload: dict,
        session_user: dict,
        mode: str,
        parsed: dict,
    ):
        if mode == "initial":
            try:
                initial_authority = resolve_owned_initial_imap_registration(
                    self.headers
                )
            except Exception:
                initial_authority = {"status": "unavailable", "error": None}
            if initial_authority.get("status") != "ok":
                authority_code = (initial_authority.get("error") or {}).get("code")
                if authority_code == "onboarding_incomplete":
                    self._send_json(
                        400,
                        _error(
                            "forbidden_client_authority",
                            "Incomplete onboarding requires the server-authoritative connection contract.",
                        ),
                    )
                    return
                unavailable = initial_authority.get("status") == "unavailable"
                self._send_json(
                    503 if unavailable else 409,
                    _error(
                        "mailbox_configuration_unavailable"
                        if unavailable
                        else "onboarding_registration_unavailable",
                        "Mailbox configuration is temporarily unavailable."
                        if unavailable
                        else "General mailbox registration is unavailable during onboarding.",
                    ),
                )
                return

        try:
            target = resolve_owned_managed_inbox_record(
                self.headers,
                parsed["mailboxId"],
            )
        except Exception:
            self._send_json(
                503,
                _error(
                    "mailbox_configuration_unavailable",
                    "Mailbox configuration is temporarily unavailable.",
                ),
            )
            return
        if mode == "initial":
            if target["status"] == "ok" or _resolved_config_contains_mailbox_id(
                target,
                parsed["mailboxId"],
            ):
                self._send_json(
                    409,
                    _error("mailbox_id_conflict", "A mailbox with this id already exists."),
                )
                return
            if target["status"] != "not_found":
                status_code = 503 if target["status"] == "unavailable" else 500
                self._send_json(
                    status_code,
                    _error(
                        "mailbox_configuration_unavailable"
                        if status_code == 503
                        else "mailbox_configuration_malformed",
                        "Mailbox configuration is temporarily unavailable."
                        if status_code == 503
                        else "Mailbox configuration is invalid.",
                    ),
                )
                return
        else:
            if target["status"] == "not_found":
                self._send_json(
                    404,
                    _error("reconnect_target_not_found", "The mailbox to reconnect was not found."),
                )
                return
            if target["status"] != "ok" or not target.get("inbox"):
                status_code = 503 if target["status"] == "unavailable" else 500
                self._send_json(
                    status_code,
                    _error(
                        "mailbox_configuration_unavailable"
                        if status_code == 503
                        else "mailbox_configuration_malformed",
                        "Mailbox configuration is temporarily unavailable."
                        if status_code == 503
                        else "Mailbox configuration is invalid.",
                    ),
                )
                return
            if target["inbox"].get("provider") != "custom_imap":
                self._send_json(
                    409,
                    _error(
                        "invalid_reconnect_target",
                        "Only an existing Custom IMAP mailbox can be reconnected.",
                    ),
                )
                return
            if target["inbox"].get("onboardingInboxId") is not None:
                try:
                    reconnect_authority = resolve_owned_initial_imap_registration(
                        self.headers
                    )
                except Exception:
                    reconnect_authority = {"status": "unavailable", "error": None}
                if reconnect_authority.get("status") != "ok":
                    unavailable = reconnect_authority.get("status") == "unavailable"
                    self._send_json(
                        503 if unavailable else 409,
                        _error(
                            "mailbox_configuration_unavailable"
                            if unavailable
                            else "onboarding_reconnect_unavailable",
                            "Mailbox configuration is temporarily unavailable."
                            if unavailable
                            else "This onboarding mailbox cannot be reconnected before onboarding is complete.",
                        ),
                        )
                    return

        previous_inbox = (
            deepcopy(target["inbox"])
            if mode == "reconnect" and isinstance(target.get("inbox"), dict)
            else None
        )
        reconnect_requires_secret_rotation = (
            mode == "reconnect"
            and (
                parsed["imap"]["password"] is not None
                or parsed["smtp"] is not None
            )
        )
        if mode == "initial":
            previous_secret = {
                "status": "missing",
                "record": None,
                "error": None,
            }
        else:
            try:
                previous_secret = (
                    snapshot_encrypted_mailbox_secret(
                        session_user["email"],
                        parsed["mailboxId"],
                    )
                    if reconnect_requires_secret_rotation
                    else {
                        "status": "not_needed",
                        "record": None,
                        "error": None,
                    }
                )
                previous_secret_read = read_mailbox_secret(
                    session_user["email"],
                    parsed["mailboxId"],
                )
            except Exception:
                previous_secret = {
                    "status": "unavailable",
                    "record": None,
                    "error": None,
                }
                previous_secret_read = {
                    "status": "unavailable",
                    "record": None,
                    "error": None,
                }
            if (
                reconnect_requires_secret_rotation
                and previous_secret.get("status") not in {"present", "missing"}
            ):
                self._send_json(
                    503,
                    _error(
                        "mailbox_secret_store_unavailable",
                        "Mailbox credential state could not be prepared.",
                    ),
                )
                return
            if not _reconnect_generation_state_is_valid(
                previous_inbox or {},
                previous_secret_read,
            ):
                status_code = (
                    503
                    if previous_secret_read.get("status")
                    in {"unavailable", "malformed"}
                    else 409
                )
                self._send_json(
                    status_code,
                    _error(
                        "mailbox_secret_store_unavailable"
                        if status_code == 503
                        else "reconnect_required",
                        "Mailbox credential state is temporarily unavailable."
                        if status_code == 503
                        else "Reconnect state changed before credentials could be updated.",
                    ),
                )
                return

        previous_secret_record = (
            previous_secret_read.get("record")
            if mode == "reconnect"
            and previous_secret_read.get("status") == "present"
            and isinstance(previous_secret_read.get("record"), dict)
            else None
        )
        effective_imap_password = parsed["imap"]["password"]
        if mode == "reconnect" and effective_imap_password is None:
            config_generation = (previous_inbox or {}).get("credentialVersion")
            existing_imap_password = (
                previous_secret_record.get("imapPassword")
                if isinstance(previous_secret_record, dict)
                else None
            )
            if (
                not is_valid_mailbox_credential_version(config_generation)
                or not _stored_password_is_usable(existing_imap_password)
            ):
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "Stored mailbox credentials cannot be reused safely.",
                    ),
                )
                return
            effective_imap_password = existing_imap_password

        preserved_smtp = None
        if mode == "reconnect" and parsed["smtp"] is None:
            preserved_smtp, _preserved_smtp_password = (
                _resolve_preserved_smtp_state(
                    previous_inbox or {},
                    previous_secret_record or {},
                )
            )
            if preserved_smtp is None:
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "Stored mailbox credentials cannot be reused safely.",
                    ),
                )
                return
        retest_preserved_same_credentials = bool(
            mode == "reconnect"
            and parsed["smtp"] is None
            and parsed["imap"]["password"] is not None
            and isinstance(preserved_smtp, dict)
            and preserved_smtp.get("useSameCredentials") is True
        )

        if not isinstance(effective_imap_password, str) or not effective_imap_password:
            self._send_json(
                409 if mode == "reconnect" else 400,
                _error(
                    "reconnect_required" if mode == "reconnect" else "invalid_request",
                    "Stored mailbox credentials cannot be reused safely."
                    if mode == "reconnect"
                    else "Complete IMAP connection details are required.",
                ),
            )
            return

        try:
            parsed["imap"]["host"] = normalize_imap_host(parsed["imap"]["host"])
        except ImapNetworkPolicyError:
            self._send_json(
                400,
                _error("imap_host_invalid", "The IMAP host is invalid."),
            )
            return
        if isinstance(parsed.get("smtp"), dict):
            try:
                parsed["smtp"]["host"] = normalize_imap_host(
                    parsed["smtp"]["host"]
                )
            except ImapNetworkPolicyError:
                self._send_json(
                    400,
                    _error("smtp_host_invalid", "The SMTP host is invalid."),
                )
                return
        if retest_preserved_same_credentials:
            try:
                preserved_smtp["host"] = normalize_imap_host(
                    preserved_smtp["host"]
                )
            except ImapNetworkPolicyError:
                self._send_json(
                    409,
                    _error(
                        "reconnect_required",
                        "Stored SMTP connection metadata cannot be reused safely.",
                    ),
                )
                return

        preview_request = {
            "mailboxId": parsed["mailboxId"],
            "provider": "custom_imap",
            "email": parsed["email"],
            "host": parsed["imap"]["host"],
            "port": str(parsed["imap"]["port"]),
            "ssl": parsed["imap"]["ssl"],
            "username": parsed["imap"]["username"],
            "password": effective_imap_password,
            "limit": parsed["limit"],
            "internalRole": parsed["internalRole"],
            "focusPreferences": parsed["focusPreferences"],
        }
        try:
            from imap_connect_preview import build_connect_preview_response

            status_code, response_payload = build_connect_preview_response(preview_request)
        except Exception:
            self._send_json(502, _error("connection_failed", "Could not connect to inbox."))
            return
        if status_code >= 400 or response_payload.get("ok") is not True:
            error_code = (response_payload.get("error") or {}).get("code")
            safe_error = _safe_imap_network_error(error_code)
            if safe_error:
                self._send_json(*safe_error)
            else:
                self._send_json(502, _error("connection_failed", "Could not connect to inbox."))
            return

        smtp_authentication_request = None
        if isinstance(parsed.get("smtp"), dict):
            parsed_smtp = parsed["smtp"]
            smtp_password = (
                effective_imap_password
                if parsed_smtp["useSameCredentials"]
                else parsed_smtp["password"]
            )
            smtp_authentication_request = {
                "host": parsed_smtp["host"],
                "port": parsed_smtp["port"],
                "security": parsed_smtp["security"],
                "username": parsed_smtp["authenticationUsername"],
                "password": smtp_password,
            }
        elif retest_preserved_same_credentials:
            smtp_authentication_request = {
                "host": preserved_smtp["host"],
                "port": _port(preserved_smtp["port"]),
                "security": preserved_smtp["security"],
                "username": parsed["imap"]["username"],
                "password": effective_imap_password,
            }

        if isinstance(smtp_authentication_request, dict):
            try:
                from smtp_connection import test_smtp_authentication

                smtp_status, smtp_payload = test_smtp_authentication(
                    smtp_authentication_request
                )
            except Exception:
                smtp_status, smtp_payload = 502, {
                    "ok": False,
                    "error": {"code": "smtp_connection_failed"},
                }
            if smtp_status >= 400 or smtp_payload.get("ok") is not True:
                error_code = (smtp_payload.get("error") or {}).get("code")
                public_code = (
                    error_code
                    if error_code
                    in {
                        "smtp_authentication_failed",
                        "smtp_connection_failed",
                        "smtp_destination_not_allowed",
                        "smtp_dns_failed",
                        "smtp_peer_mismatch",
                        "smtp_tls_failed",
                    }
                    else "smtp_connection_failed"
                )
                self._send_json(
                    400
                    if public_code == "smtp_destination_not_allowed"
                    else 502,
                    _error(
                        public_code,
                        "SMTP authentication failed."
                        if public_code == "smtp_authentication_failed"
                        else "A secure SMTP connection could not be established.",
                    ),
                )
                return

        rotate_credentials = (
            mode == "initial"
            or parsed["imap"]["password"] is not None
            or parsed["smtp"] is not None
        )

        if mode == "initial":
            try:
                namespace_snapshot = snapshot_mailbox_secret_namespace(
                    session_user["email"],
                    parsed["mailboxId"],
                )
            except Exception:
                namespace_snapshot = {
                    "status": "unavailable",
                    "record": None,
                    "error": None,
                }
            if namespace_snapshot.get("status") == "present":
                self._send_json(
                    409,
                    _error(
                        "mailbox_id_conflict",
                        "A mailbox with this id already exists.",
                    ),
                )
                return
            if namespace_snapshot.get("status") != "missing":
                self._send_json(
                    503,
                    _error(
                        "mailbox_secret_store_unavailable",
                        "Mailbox credential state could not be prepared.",
                    ),
                )
                return

        credential_version = (
            generate_mailbox_credential_version()
            if rotate_credentials
            else (previous_inbox or {}).get("credentialVersion")
        )
        if not is_valid_mailbox_credential_version(credential_version):
            self._send_json(
                503,
                _error(
                    "mailbox_secret_store_unavailable",
                    "Mailbox credential state could not be prepared.",
                ),
            )
            return

        smtp_password_update = None
        if parsed["smtp"] is not None:
            smtp_password_update = (
                ""
                if parsed["smtp"]["useSameCredentials"]
                else parsed["smtp"]["password"]
            )
        elif retest_preserved_same_credentials:
            smtp_password_update = ""
        expected_mailbox = _build_expected_credential_mailbox(
            parsed,
            payload,
            previous_inbox,
            credential_version,
        )
        if rotate_credentials:
            try:
                saved_secret, save_error = save_mailbox_secret(
                    session_user["email"],
                    parsed["mailboxId"],
                    imap_password=parsed["imap"]["password"],
                    smtp_password=smtp_password_update,
                    credential_version=credential_version,
                    expected_snapshot=previous_secret,
                    require_namespace_missing=mode == "initial",
                )
            except Exception:
                saved_secret, save_error = None, {
                    "code": "mailbox_secret_store_unavailable"
                }
            if save_error or not isinstance(saved_secret, dict):
                self._send_secret_write_error(save_error)
                return
            expected_secret_record = saved_secret
        else:
            expected_secret_record = previous_secret_record

        connection_metadata = {
            "email": parsed["email"],
            "customImap": {
                "host": parsed["imap"]["host"],
                "port": str(parsed["imap"]["port"]),
                "ssl": parsed["imap"]["ssl"],
                "username": parsed["imap"]["username"],
            },
            "customSmtp": deepcopy(expected_mailbox["customSmtp"]),
        }
        if isinstance(parsed.get("smtp"), dict):
            connection_metadata.update(
                {
                    "imapConnectionStatus": "connected",
                    "smtpConnectionStatus": "connected",
                    "fullyConnected": True,
                }
            )
        try:
            upsert_result = upsert_owned_custom_imap_mailbox(
                self.headers,
                parsed["mailboxId"],
                mode,
                connection_metadata,
                {
                    key: payload[key]
                    for key in ("internalRole", "focusPreferences")
                    if key in payload
                },
                credential_version=credential_version,
                expected_inbox=previous_inbox,
                require_completed_onboarding=mode == "initial",
            )
        except Exception:
            upsert_result = {"status": "unavailable", "error": None}

        try:
            config_readback = resolve_owned_managed_inbox_record(
                self.headers,
                parsed["mailboxId"],
            )
        except Exception:
            config_readback = {
                "status": "unavailable",
                "inbox": None,
                "config": None,
            }
        try:
            secret_readback = read_mailbox_secret(
                session_user["email"],
                parsed["mailboxId"],
            )
        except Exception:
            secret_readback = {
                "status": "unavailable",
                "record": None,
                "error": None,
            }
        if _mailbox_readback_is_exact(
            config_readback,
            expected_mailbox,
        ) and isinstance(expected_secret_record, dict) and _secret_readback_is_exact(
            secret_readback,
            expected_secret_record.get("credentialVersion"),
            expected_secret_record.get("imapPassword"),
            expected_secret_record.get("smtpPassword"),
        ):
            self._send_json(200, _preview_success_payload(response_payload))
            return

        try:
            config_rollback_error = rollback_owned_custom_imap_mailbox_update(
                self.headers,
                parsed["mailboxId"],
                expected_mailbox,
                previous_inbox,
            )
        except Exception:
            config_rollback_error = {
                "code": "user_config_store_unavailable",
                "message": "Mailbox configuration could not be restored safely.",
            }
        if config_rollback_error:
            if (
                rotate_credentials
                and isinstance(config_rollback_error, dict)
                and config_rollback_error.get("code")
                == "user_config_newer_mailbox_preserved"
            ):
                try:
                    secret_rollback_error = (
                        restore_encrypted_mailbox_secret_snapshot(
                            session_user["email"],
                            parsed["mailboxId"],
                            previous_secret,
                            expected_credential_version=credential_version,
                        )
                    )
                except Exception:
                    secret_rollback_error = {
                        "code": "mailbox_secret_store_unavailable",
                        "message": (
                            "Mailbox connection state could not be restored safely."
                        ),
                    }
                if (
                    secret_rollback_error
                    and (
                        not isinstance(secret_rollback_error, dict)
                        or secret_rollback_error.get("code")
                        != "mailbox_secret_write_conflict"
                    )
                ):
                    self._send_rollback_error(secret_rollback_error)
                    return
            self._send_rollback_error(config_rollback_error)
            return
        if rotate_credentials:
            try:
                secret_rollback_error = restore_encrypted_mailbox_secret_snapshot(
                    session_user["email"],
                    parsed["mailboxId"],
                    previous_secret,
                    expected_credential_version=credential_version,
                )
            except Exception:
                secret_rollback_error = {
                    "code": "mailbox_secret_store_unavailable",
                    "message": "Mailbox connection state could not be restored safely.",
                }
            if secret_rollback_error:
                self._send_rollback_error(secret_rollback_error)
                return

        if upsert_result.get("status") == "conflict":
            provider_mismatch = (
                (upsert_result.get("error") or {}).get("code")
                == "managed_inbox_provider_mismatch"
            )
            self._send_json(
                409,
                _error(
                    "invalid_reconnect_target"
                    if provider_mismatch
                    else "mailbox_id_conflict",
                    "Only an existing Custom IMAP mailbox can be reconnected."
                    if provider_mismatch
                    else "A mailbox with this id already exists.",
                ),
            )
            return
        if upsert_result.get("status") == "not_found":
            self._send_json(
                404,
                _error(
                    "reconnect_target_not_found",
                    "The mailbox to reconnect was not found.",
                ),
            )
            return
        self._send_json(
            503,
            _error(
                "configuration_persistence_failed"
                if upsert_result.get("status") == "ok"
                else "user_config_store_unavailable",
                "Mailbox configuration could not be verified."
                if upsert_result.get("status") == "ok"
                else "Mailbox configuration could not be stored.",
            ),
        )

    def _handle_refresh(self, payload: dict):
        if not _has_only_fields(payload, REFRESH_FIELDS):
            self._send_json(400, _error("invalid_request", "Refresh request is invalid."))
            return
        mailbox_id = _valid_mailbox_id(payload.get("mailboxId"))
        limit = payload.get("limit", 20)
        if (
            not mailbox_id
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 100
        ):
            self._send_json(400, _error("invalid_request", "Refresh request is invalid."))
            return

        resolved = resolve_authenticated_imap_mailbox(self.headers, mailbox_id)
        if resolved["status"] != "ok" or not resolved["mailbox"]:
            error = resolved["error"] or {
                "code": "mailbox_configuration_malformed",
                "message": "Mailbox configuration is invalid.",
                "status_code": 500,
            }
            self._send_json(error["status_code"], _error(error["code"], error["message"]))
            return

        mailbox = resolved["mailbox"]
        try:
            normalized_host = normalize_imap_host(mailbox["imap"]["host"])
        except (ImapNetworkPolicyError, KeyError, TypeError):
            self._send_json(
                400,
                _error("imap_host_invalid", "The IMAP host is invalid."),
            )
            return
        preview_request = {
            "mailboxId": mailbox_id,
            "provider": "custom_imap",
            "email": mailbox["email"],
            "host": normalized_host,
            "port": str(mailbox["imap"]["port"]),
            "ssl": mailbox["imap"]["ssl"],
            "username": mailbox["imap"]["username"],
            "password": mailbox["imap"]["password"],
            "limit": limit,
            "focusPreferences": payload.get("focusPreferences"),
            "internalRole": None,
        }
        try:
            from imap_connect_preview import build_connect_preview_response

            status_code, response_payload = build_connect_preview_response(preview_request)
        except Exception:
            self._send_json(502, _error("connection_failed", "Could not refresh this inbox."))
            return
        if status_code >= 400 or response_payload.get("ok") is not True:
            error_code = (response_payload.get("error") or {}).get("code")
            safe_error = _safe_imap_network_error(error_code)
            if safe_error:
                self._send_json(*safe_error)
            else:
                self._send_json(502, _error("connection_failed", "Could not refresh this inbox."))
            return
        success_payload = _preview_success_payload(response_payload)
        success_payload["prioritySemanticNewInboundMode"] = (
            read_new_inbound_client_mode()
        )
        self._send_json(200, success_payload)

    def do_GET(self):
        self._send_json(405, _error("method_not_allowed", "Use POST for inbox connection."))

    def log_message(self, format, *args):
        return
