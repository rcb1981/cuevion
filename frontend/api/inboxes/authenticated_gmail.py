from __future__ import annotations

import importlib as _identity_importlib
import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.authenticated_gmail"
_LEGACY_MODULE_NAME = "authenticated_gmail"
_FORWARD_MARKER = "_cuevion_forward_to_canonical_module"

if __name__ == _LEGACY_MODULE_NAME:
    _identity_sys.modules[__name__].__dict__[_FORWARD_MARKER] = (
        _CANONICAL_MODULE_NAME
    )
    _canonical_module = _identity_importlib.import_module(_CANONICAL_MODULE_NAME)
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _canonical_module
elif __name__ != _CANONICAL_MODULE_NAME:
    raise ImportError(
        "Gmail helpers must be imported as " + _CANONICAL_MODULE_NAME
    )
else:
    _legacy_module = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
    if (
        _legacy_module is not None
        and _legacy_module is not _identity_sys.modules[__name__]
        and getattr(_legacy_module, _FORWARD_MARKER, None)
        != _CANONICAL_MODULE_NAME
    ):
        raise ImportError("canonical and legacy Gmail provider identities cannot coexist")
    _identity_sys.modules[_LEGACY_MODULE_NAME] = _identity_sys.modules[__name__]

    import json
    from datetime import datetime, timezone
    from http.server import BaseHTTPRequestHandler
    from typing import Iterable, TypedDict

    from .oauth_token_store import (
        load_google_token_record_with_metadata,
        refresh_google_token_record,
    )
    from ..user_config_store import resolve_owned_managed_inbox_record

    MAX_SMALL_REQUEST_BODY_BYTES = 16 * 1024
    # Accommodates two 2 MiB Unicode bodies plus 8 MiB of attachments after
    # base64 expansion and bounded JSON/metadata overhead.
    MAX_SEND_REQUEST_BODY_BYTES = 32 * 1024 * 1024
    MAX_IDENTIFIER_LENGTH = 256
    MAX_GMAIL_RESPONSE_BYTES = 10 * 1024 * 1024
    MAX_GMAIL_RAW_MESSAGE_BYTES = 25 * 1024 * 1024
    FOCUS_PREFERENCE_KEYS = {
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
    }
    FOCUS_PREFERENCE_LEVELS = {"high", "medium", "low"}
    MAX_FOCUS_PREFERENCE_ENTRIES = len(FOCUS_PREFERENCE_KEYS)
    MAX_FOCUS_PREFERENCE_KEY_LENGTH = 32
    MAX_FOCUS_PREFERENCE_VALUE_LENGTH = 16
    MAX_FOCUS_PREFERENCE_TOTAL_STRING_LENGTH = 256

    FORBIDDEN_IDENTITY_FIELDS = {
        "email",
        "provider",
        "authMode",
        "from",
        "username",
        "password",
        "host",
        "port",
        "smtpHost",
        "smtpPort",
        "smtpUsername",
        "smtpPassword",
        "accessToken",
        "access_token",
        "refreshToken",
        "refresh_token",
        "ownerEmail",
        "owner_email",
        "userId",
        "user_id",
    }


    class GmailContext(TypedDict):
        mailbox_id: str
        mailbox_email: str
        owner_email: str
        access_token: str
        scope: str | None
        refresh_attempted: bool


    def error_payload(code: str, message: str) -> dict:
        return {"ok": False, "error": {"code": code, "message": message}}


    def result_error(status_code: int, code: str, message: str) -> dict:
        return {
            "status": "error",
            "status_code": status_code,
            "error": error_payload(code, message),
        }


    def send_json(
        handler: BaseHTTPRequestHandler,
        status_code: int,
        payload: dict,
        *,
        write_body: bool = True,
    ):
        response_body = json.dumps(payload).encode("utf-8")
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(response_body)))
        handler.end_headers()
        if write_body:
            handler.wfile.write(response_body)


    def send_method_not_allowed(
        handler: BaseHTTPRequestHandler,
        message: str,
        *,
        write_body: bool = True,
    ):
        send_json(
            handler,
            405,
            error_payload("method_not_allowed", message),
            write_body=write_body,
        )


    def read_json_body(
        handler: BaseHTTPRequestHandler,
        *,
        max_bytes: int = MAX_SMALL_REQUEST_BODY_BYTES,
    ) -> tuple[dict | None, dict | None]:
        try:
            content_length = int(handler.headers.get("content-length", "0"))
        except (TypeError, ValueError):
            return None, error_payload("invalid_request", "Content-Length must be valid.")
        if content_length < 0:
            return None, error_payload("invalid_request", "Content-Length must not be negative.")
        if content_length > max_bytes:
            return None, error_payload("request_too_large", "Request body is too large.")

        raw_body = handler.rfile.read(content_length) if content_length else b""
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, error_payload("invalid_request", "Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return None, error_payload("invalid_request", "Request body must be a JSON object.")
        return payload, None


    def reject_unknown_fields(payload: dict, allowed_fields: Iterable[str]) -> dict | None:
        allowed = set(allowed_fields)
        if set(payload) - allowed or FORBIDDEN_IDENTITY_FIELDS.intersection(payload):
            return error_payload("invalid_request", "Request contains unsupported fields.")
        return None


    def valid_identifier(value: object) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
            and value == value.strip()
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )


    def validate_focus_preferences(value: object) -> tuple[dict | None, dict | None]:
        if not isinstance(value, dict):
            return None, error_payload(
                "invalid_focus_preferences",
                "Focus preferences must be an object.",
            )
        if len(value) > MAX_FOCUS_PREFERENCE_ENTRIES:
            return None, error_payload(
                "invalid_focus_preferences",
                "Focus preferences contain too many entries.",
            )

        validated: dict[str, str] = {}
        total_string_length = 0
        for key, preference in value.items():
            if (
                not isinstance(key, str)
                or key not in FOCUS_PREFERENCE_KEYS
                or len(key) > MAX_FOCUS_PREFERENCE_KEY_LENGTH
                or not isinstance(preference, str)
                or len(preference) > MAX_FOCUS_PREFERENCE_VALUE_LENGTH
                or preference not in FOCUS_PREFERENCE_LEVELS
            ):
                return None, error_payload(
                    "invalid_focus_preferences",
                    "Focus preferences contain an unsupported value.",
                )
            total_string_length += len(key) + len(preference)
            if total_string_length > MAX_FOCUS_PREFERENCE_TOTAL_STRING_LENGTH:
                return None, error_payload(
                    "invalid_focus_preferences",
                    "Focus preferences are too large.",
                )
            validated[key] = preference
        return validated, None


    def gmail_http_error_code(status_code: int, default_code: str) -> str:
        if status_code == 401:
            return "gmail_token_invalid"
        if status_code == 403:
            return "gmail_permission_denied"
        if status_code == 429:
            return "gmail_rate_limited"
        return default_code


    def _token_expiry_status(token_record: dict) -> str:
        expires_at = token_record.get("expires_at")
        if expires_at is None:
            return "valid"
        if not isinstance(expires_at, str) or not expires_at.strip():
            return "malformed"
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return "malformed"
        return "expired" if parsed <= datetime.now(timezone.utc) else "valid"


    def is_token_expired(token_record: dict) -> bool:
        return _token_expiry_status(token_record) == "expired"


    def resolve_owned_mailbox(
        headers,
        mailbox_id: object,
        *,
        include_member_authority: bool = False,
    ) -> dict:
        if not valid_identifier(mailbox_id):
            return result_error(400, "invalid_mailbox_id", "Mailbox id is invalid.")

        owned = (
            resolve_owned_managed_inbox_record(
                headers,
                mailbox_id,
                include_member_authority=True,
            )
            if include_member_authority
            else resolve_owned_managed_inbox_record(headers, mailbox_id)
        )
        if owned["status"] != "ok" or not owned.get("user") or not owned.get("inbox"):
            status = owned.get("status")
            if status == "unauthorized":
                return result_error(401, "unauthorized", "A valid member session is required.")
            if status == "not_found":
                return result_error(404, "gmail_connection_not_found", "Mailbox connection was not found.")
            if status in {"unavailable", "malformed"}:
                return result_error(
                    503,
                    "user_config_store_unavailable",
                    "User config storage is temporarily unavailable.",
                )
            return result_error(
                503,
                "mailbox_ownership_unavailable",
                "Mailbox ownership could not be verified.",
            )

        result = {
            "status": "ok",
            "user": owned["user"],
            "inbox": owned["inbox"],
        }
        if include_member_authority:
            member = owned.get("memberAuthority")
            config = owned.get("config")
            if member is None or not isinstance(config, dict):
                return result_error(
                    503,
                    "mailbox_ownership_unavailable",
                    "Mailbox ownership could not be verified.",
                )
            result["memberAuthority"] = member
            result["config"] = config
        return result


    def _token_failure(error: dict | None = None) -> dict:
        code = (error or {}).get("code")
        if error is None or code in {
            "gmail_reconnect_required",
            "gmail_refresh_invalid_grant",
            "gmail_refresh_token_missing",
            "gmail_token_missing",
            "gmail_token_record_malformed",
        }:
            return result_error(
                401,
                "reconnect_required",
                "Reconnect this Gmail inbox to continue.",
            )
        if code in {"gmail_token_store_unavailable", "token_persistence_failed"}:
            return result_error(
                503,
                "gmail_token_store_unavailable",
                "Gmail authorization storage is temporarily unavailable.",
            )
        if code == "gmail_refresh_not_configured":
            return result_error(
                503,
                "gmail_refresh_not_configured",
                "Gmail authorization refresh is not configured.",
            )
        if code == "gmail_refresh_rate_limited":
            return result_error(
                429,
                "gmail_refresh_rate_limited",
                "Gmail authorization refresh is temporarily rate limited.",
            )
        if code in {"gmail_refresh_unavailable", "gmail_refresh_failed"}:
            return result_error(
                502,
                "gmail_refresh_unavailable",
                "Gmail authorization refresh is temporarily unavailable.",
            )
        if code == "gmail_token_write_conflict":
            return result_error(
                503,
                "gmail_refresh_conflict",
                "Gmail authorization changed while it was being refreshed.",
            )
        return result_error(
            502,
            "gmail_refresh_unavailable",
            "Gmail authorization refresh is temporarily unavailable.",
        )


    def resolve_gmail_context(owned: dict) -> dict:
        user = owned.get("user")
        inbox = owned.get("inbox")
        if not isinstance(user, dict) or not isinstance(inbox, dict):
            return result_error(503, "mailbox_ownership_unavailable", "Mailbox ownership could not be verified.")
        if inbox.get("provider") != "google":
            return result_error(400, "unsupported_provider", "This mailbox is not a Gmail connection.")
        if inbox.get("connected") is not True or inbox.get("connectionStatus") != "connected":
            return result_error(409, "gmail_connection_not_ready", "Gmail connection is not ready.")

        mailbox_email = inbox.get("email")
        owner_email = user.get("email")
        if (
            not isinstance(mailbox_email, str)
            or not mailbox_email.strip()
            or not isinstance(owner_email, str)
            or not owner_email.strip()
        ):
            return result_error(503, "mailbox_ownership_unavailable", "Mailbox ownership could not be verified.")
        mailbox_email = mailbox_email.strip().lower()
        owner_email = owner_email.strip().lower()

        token_record, token_error = load_google_token_record_with_metadata(
            mailbox_email,
            owner_email=owner_email,
        )
        if token_error:
            return _token_failure(token_error)
        if not isinstance(token_record, dict):
            return _token_failure()
        if (
            token_record.get("provider") != "google"
            or str(token_record.get("email") or "").strip().lower() != mailbox_email
            or not isinstance(token_record.get("owner_email"), str)
            or not token_record["owner_email"].strip()
            or token_record["owner_email"].strip().lower() != owner_email
            or token_record.get("_storage_durable") is not True
        ):
            return _token_failure()

        expiry_status = _token_expiry_status(token_record)
        if expiry_status == "malformed":
            return _token_failure()

        access_token = token_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return _token_failure()
        scope = token_record.get("scope")

        context: GmailContext = {
            "mailbox_id": inbox["id"],
            "mailbox_email": mailbox_email,
            "owner_email": owner_email,
            "access_token": access_token.strip(),
            "scope": scope if isinstance(scope, str) else None,
            "refresh_attempted": False,
        }
        if expiry_status == "expired":
            refreshed = refresh_gmail_context(context)
            if refreshed["status"] != "ok":
                return refreshed
            context = refreshed["context"]
        return {"status": "ok", "context": context}


    def resolve_authenticated_gmail(
        headers,
        mailbox_id: object,
        *,
        include_member_authority: bool = False,
    ) -> dict:
        owned = resolve_owned_mailbox(
            headers,
            mailbox_id,
            include_member_authority=include_member_authority,
        )
        if owned["status"] != "ok":
            return owned
        result = resolve_gmail_context(owned)
        if result.get("status") == "ok" and include_member_authority:
            result["memberAuthority"] = owned["memberAuthority"]
        return result


    def refresh_gmail_context(context: GmailContext) -> dict:
        if context.get("refresh_attempted"):
            return _token_failure()
        refreshed_record, refresh_error = refresh_google_token_record(
            context["mailbox_email"],
            owner_email=context["owner_email"],
        )
        if refresh_error or not isinstance(refreshed_record, dict):
            return _token_failure(refresh_error)
        if str(refreshed_record.get("owner_email") or "").strip().lower() != context["owner_email"]:
            return _token_failure()
        access_token = refreshed_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return _token_failure()
        refreshed_scope = refreshed_record.get("scope")
        if not isinstance(refreshed_scope, str):
            refreshed_scope = context.get("scope")
        return {
            "status": "ok",
            "context": {
                **context,
                "access_token": access_token.strip(),
                "scope": refreshed_scope,
                "refresh_attempted": True,
            },
        }


    def read_bounded_response(response, max_bytes: int) -> bytes | None:
        content_length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    return None
            except ValueError:
                pass
        body = response.read(max_bytes + 1)
        return body if len(body) <= max_bytes else None
