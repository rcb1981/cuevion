from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.authenticated_imap"
_LEGACY_MODULE_NAME = "authenticated_imap"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "IMAP helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy IMAP provider identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import re
    from typing import Literal, TypedDict

    from .mailbox_secret_store import (
        is_valid_mailbox_credential_version,
        read_mailbox_secret,
    )
    from ..user_config_store import (
        CUSTOM_IMAP_FOLDER_MAPPINGS_FIELD,
        resolve_owned_managed_inbox_record,
        validate_custom_imap_folder_mappings,
    )

    FORBIDDEN_CUSTOM_REQUEST_FIELDS = {
        "password",
        "provider",
        "email",
        "from",
        "host",
        "port",
        "ssl",
        "username",
        "imapHost",
        "imapPort",
        "imapUsername",
        "smtpHost",
        "smtpPort",
        "smtpSecurity",
        "authMode",
        "useSameCredentials",
        "customImap",
        "customSmtp",
        "connection",
        "credentialVersion",
        "secretVersion",
        "credentialGeneration",
        "secretGeneration",
        "customImapFolderMappings",
        "trashFolder",
        "archiveFolder",
    }


    class AuthenticatedImapError(TypedDict):
        code: str
        message: str
        status_code: int


    class AuthenticatedImapResult(TypedDict):
        status: Literal[
            "ok",
            "unauthorized",
            "not_found",
            "reconnect_required",
            "service_unavailable",
            "malformed",
        ]
        mailbox: dict | None
        error: AuthenticatedImapError | None


    def _failure(
        status: AuthenticatedImapResult["status"],
        code: str,
        message: str,
        status_code: int,
    ) -> AuthenticatedImapResult:
        return {
            "status": status,
            "mailbox": None,
            "error": {"code": code, "message": message, "status_code": status_code},
        }


    def find_forbidden_custom_request_fields(payload: dict) -> list[str]:
        found: set[str] = set()

        def visit(value):
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
                        if key in FORBIDDEN_CUSTOM_REQUEST_FIELDS or is_generation:
                            found.add(key)
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return sorted(found)


    def configured_imap_trash_folder(
        value: object,
    ) -> tuple[str | None, str | None]:
        """Extract only a strictly versioned, server-owned Trash mapping."""
        if value is None:
            return None, None
        mappings = validate_custom_imap_folder_mappings(value)
        if mappings is None:
            return None, "mailbox_configuration_malformed"
        return mappings["trashFolder"], None


    def _exact_string(value, *, allow_empty: bool = False) -> str | None:
        if not isinstance(value, str) or value != value.strip():
            return None
        if not allow_empty and not value:
            return None
        if "\r" in value or "\n" in value:
            return None
        return value


    def _valid_host(value) -> str | None:
        host = _exact_string(value)
        if not host or any(character.isspace() for character in host):
            return None
        return host


    def _valid_port(value) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            port = int(str(value))
        except (TypeError, ValueError):
            return None
        return port if 1 <= port <= 65535 else None


    def _valid_email(value) -> str | None:
        email = _exact_string(value)
        if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+", email):
            return None
        return email


    def resolve_authenticated_imap_mailbox(
        headers,
        mailbox_id: str,
        *,
        require_smtp: bool = False,
    ) -> AuthenticatedImapResult:
        owned_result = resolve_owned_managed_inbox_record(headers, mailbox_id)
        if owned_result["status"] == "unauthorized":
            return _failure(
                "unauthorized",
                "unauthorized",
                "A valid member session is required.",
                401,
            )
        if owned_result["status"] == "not_found":
            return _failure(
                "not_found",
                "managed_inbox_not_found",
                "The requested mailbox was not found.",
                404,
            )
        if owned_result["status"] == "unavailable":
            return _failure(
                "service_unavailable",
                "mailbox_configuration_unavailable",
                "Mailbox configuration is temporarily unavailable.",
                503,
            )
        if owned_result["status"] != "ok" or not owned_result["inbox"] or not owned_result["user"]:
            return _failure(
                "malformed",
                "mailbox_configuration_malformed",
                "Mailbox configuration is invalid.",
                500,
            )

        inbox = owned_result["inbox"]
        if inbox.get("provider") != "custom_imap":
            return _failure(
                "not_found",
                "managed_inbox_not_found",
                "The requested mailbox was not found.",
                404,
            )
        if inbox.get("connected") is not True or inbox.get("connectionStatus") != "connected":
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )
        if inbox.get("imapConnectionStatus") not in {None, "connected"}:
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )
        folder_mappings = None
        if CUSTOM_IMAP_FOLDER_MAPPINGS_FIELD in inbox:
            folder_mappings = validate_custom_imap_folder_mappings(
                inbox.get(CUSTOM_IMAP_FOLDER_MAPPINGS_FIELD)
            )
            if folder_mappings is None:
                return _failure(
                    "malformed",
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                    500,
                )
        config_credential_version = inbox.get("credentialVersion")
        if not is_valid_mailbox_credential_version(config_credential_version):
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )

        email = _valid_email(inbox.get("email"))
        custom_imap = inbox.get("customImap")
        custom_smtp = inbox.get("customSmtp")
        if not isinstance(custom_imap, dict) or not isinstance(custom_smtp, dict):
            return _failure(
                "malformed",
                "mailbox_configuration_malformed",
                "Mailbox configuration is invalid.",
                500,
            )

        imap_host = _valid_host(custom_imap.get("host"))
        imap_port = _valid_port(custom_imap.get("port"))
        imap_username = _exact_string(custom_imap.get("username")) or email
        imap_ssl = custom_imap.get("ssl")
        smtp_host = _valid_host(custom_smtp.get("host"))
        smtp_port = _valid_port(custom_smtp.get("port"))
        smtp_security = custom_smtp.get("security")
        use_same_credentials = custom_smtp.get("useSameCredentials") is True
        smtp_username = (
            imap_username
            if use_same_credentials
            else _exact_string(custom_smtp.get("username"))
        )

        if (
            not email
            or not imap_host
            or not imap_port
            or not imap_username
            or imap_ssl is not True
        ):
            return _failure(
                "malformed",
                "mailbox_configuration_malformed",
                "Mailbox configuration is invalid.",
                500,
            )
        if require_smtp and (
            inbox.get("imapConnectionStatus") != "connected"
            or inbox.get("smtpConnectionStatus") != "connected"
            or inbox.get("fullyConnected") is not True
            or not smtp_host
            or not smtp_port
            or (smtp_security == "ssl" and smtp_port != 465)
            or (smtp_security == "starttls" and smtp_port != 587)
            or smtp_security not in {"ssl", "starttls"}
            or not smtp_username
        ):
            return _failure(
                "malformed",
                "mailbox_configuration_malformed",
                "Mailbox configuration is invalid.",
                500,
            )

        secret_result = read_mailbox_secret(owned_result["user"]["email"], mailbox_id)
        if secret_result["status"] == "missing":
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )
        if secret_result["status"] == "unavailable":
            return _failure(
                "service_unavailable",
                "mailbox_secret_store_unavailable",
                "Mailbox credentials are temporarily unavailable.",
                503,
            )
        if secret_result["status"] != "present" or not secret_result["record"]:
            return _failure(
                "malformed",
                "mailbox_secret_malformed",
                "Stored mailbox credentials are invalid.",
                500,
            )

        secrets = secret_result["record"]
        secret_credential_version = secrets.get("credentialVersion")
        if (
            not is_valid_mailbox_credential_version(secret_credential_version)
            or secret_credential_version != config_credential_version
        ):
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )
        imap_password = secrets.get("imapPassword")
        smtp_password = (
            imap_password if use_same_credentials else secrets.get("smtpPassword")
        )
        if not isinstance(imap_password, str) or not imap_password:
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )
        if require_smtp and (not isinstance(smtp_password, str) or not smtp_password):
            return _failure(
                "reconnect_required",
                "reconnect_required",
                "Reconnect this mailbox to continue.",
                409,
            )

        return {
            "status": "ok",
            "mailbox": {
                "mailboxId": mailbox_id,
                "ownerEmail": owned_result["user"]["email"],
                "email": email,
                "customImapFolderMappings": folder_mappings,
                "imap": {
                    "host": imap_host,
                    "port": imap_port,
                    "ssl": imap_ssl,
                    "username": imap_username,
                    "password": imap_password,
                },
                "smtp": {
                    "host": smtp_host,
                    "port": smtp_port,
                    "security": smtp_security,
                    "username": smtp_username,
                    "password": smtp_password,
                    "useSameCredentials": use_same_credentials,
                },
            },
            "error": None,
        }
