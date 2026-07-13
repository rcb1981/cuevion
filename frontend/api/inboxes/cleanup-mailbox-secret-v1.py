from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from mailbox_secret_store import cleanup_legacy_mailbox_secret_v1  # noqa: E402
from user_config_store import (  # noqa: E402
    resolve_authenticated_user,
    resolve_owned_managed_inbox_record,
)

MAX_REQUEST_BODY_BYTES = 16_384

# Temporary migration endpoint: remove after verified cleanup of the migrated active mailboxes.


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _send_json(
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
    handler.send_header("Content-Length", str(len(response_body) if write_body else 0))
    handler.end_headers()
    if write_body:
        handler.wfile.write(response_body)


def _send_method_not_allowed(
    handler: BaseHTTPRequestHandler,
    *,
    write_body: bool = True,
):
    _send_json(
        handler,
        405,
        _error(
            "method_not_allowed",
            "Use POST for legacy mailbox credential cleanup.",
        ),
        write_body=write_body,
    )


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    try:
        content_length = int(handler.headers.get("content-length", "0"))
    except (TypeError, ValueError):
        return None, _error("invalid_request", "Request body is invalid.")
    if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
        return None, _error("invalid_request", "Request body is invalid.")

    try:
        raw_body = handler.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_body or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error("invalid_request", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return None, _error("invalid_request", "Request body must be a JSON object.")
    return payload, None


def _validated_mailbox_id(payload: dict) -> str | None:
    if set(payload) != {"mailboxId"}:
        return None
    mailbox_id = payload.get("mailboxId")
    if (
        not isinstance(mailbox_id, str)
        or not mailbox_id
        or mailbox_id != mailbox_id.strip()
        or mailbox_id.startswith("draft-")
        or "\r" in mailbox_id
        or "\n" in mailbox_id
    ):
        return None
    return mailbox_id


def _owned_mailbox_failure(result: dict) -> tuple[int, dict]:
    error_code = (result.get("error") or {}).get("code")
    status = result.get("status")
    if status == "unauthorized":
        return 401, _error("unauthorized", "A valid beta session is required.")
    if status == "not_found":
        return 404, _error(
            "managed_inbox_not_found",
            "The requested mailbox was not found.",
        )
    if error_code == "duplicate_mailbox_id":
        return 409, _error(
            "duplicate_mailbox_id",
            "Mailbox configuration contains duplicate ids.",
        )
    if status == "unavailable":
        return 503, _error(
            "mailbox_configuration_unavailable",
            "Mailbox configuration is temporarily unavailable.",
        )
    return 500, _error(
        "mailbox_configuration_malformed",
        "Mailbox configuration is invalid.",
    )


CLEANUP_FAILURES = {
    "invalid": (
        500,
        "mailbox_secret_cleanup_invalid",
        "Mailbox secret cleanup could not be validated.",
    ),
    "v2_missing": (
        409,
        "mailbox_secret_v2_missing",
        "Encrypted mailbox credentials were not found.",
    ),
    "v2_malformed": (
        500,
        "mailbox_secret_v2_malformed",
        "Encrypted mailbox credentials are invalid.",
    ),
    "v2_decryption_failed": (
        500,
        "mailbox_secret_v2_decryption_failed",
        "Encrypted mailbox credentials could not be decrypted.",
    ),
    "v2_unusable": (
        409,
        "mailbox_secret_v2_unusable",
        "Encrypted mailbox credentials are incomplete.",
    ),
    "encryption_unavailable": (
        503,
        "mailbox_secret_encryption_unavailable",
        "Mailbox secret encryption is unavailable.",
    ),
    "storage_unavailable": (
        503,
        "mailbox_secret_store_unavailable",
        "Mailbox secret storage is temporarily unavailable.",
    ),
    "delete_failed": (
        503,
        "mailbox_secret_v1_delete_failed",
        "Legacy mailbox credentials could not be removed.",
    ),
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload, body_error = _read_json_body(self)
        if body_error or payload is None:
            _send_json(
                self,
                400,
                body_error or _error("invalid_request", "Request body is invalid."),
            )
            return
        mailbox_id = _validated_mailbox_id(payload)
        if not mailbox_id:
            _send_json(
                self,
                400,
                _error(
                    "invalid_request",
                    "Request must contain exactly one valid mailboxId.",
                ),
            )
            return

        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not isinstance(session_user, dict):
            if auth_error and auth_error.get("code") == "session_auth_unavailable":
                _send_json(
                    self,
                    503,
                    _error(
                        "session_auth_unavailable",
                        "Authentication is temporarily unavailable.",
                    ),
                )
            else:
                _send_json(
                    self,
                    401,
                    _error("unauthorized", "A valid beta session is required."),
                )
            return

        try:
            owned_result = resolve_owned_managed_inbox_record(self.headers, mailbox_id)
        except Exception:
            _send_json(
                self,
                503,
                _error(
                    "mailbox_configuration_unavailable",
                    "Mailbox configuration is temporarily unavailable.",
                ),
            )
            return
        if not isinstance(owned_result, dict):
            _send_json(
                self,
                500,
                _error(
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                ),
            )
            return
        if (
            owned_result.get("status") != "ok"
            or not owned_result.get("user")
            or not owned_result.get("inbox")
        ):
            status_code, response = _owned_mailbox_failure(owned_result)
            _send_json(self, status_code, response)
            return

        resolved_user = owned_result["user"]
        inbox = owned_result["inbox"]
        if (
            not isinstance(resolved_user, dict)
            or not isinstance(inbox, dict)
            or not isinstance(session_user.get("email"), str)
            or resolved_user.get("email") != session_user["email"]
            or inbox.get("id") != mailbox_id
        ):
            _send_json(
                self,
                500,
                _error(
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                ),
            )
            return
        if inbox.get("provider") != "custom_imap":
            _send_json(
                self,
                409,
                _error(
                    "invalid_mailbox_provider",
                    "Only a Custom IMAP mailbox can be cleaned up.",
                ),
            )
            return
        if inbox.get("connected") is not True or inbox.get("connectionStatus") != "connected":
            _send_json(
                self,
                409,
                _error(
                    "mailbox_not_connected",
                    "The mailbox must be connected before cleanup.",
                ),
            )
            return

        custom_smtp = inbox.get("customSmtp")
        use_same_credentials = (
            custom_smtp.get("useSameCredentials")
            if isinstance(custom_smtp, dict)
            else None
        )
        if type(use_same_credentials) is not bool:
            _send_json(
                self,
                500,
                _error(
                    "mailbox_configuration_malformed",
                    "Mailbox configuration is invalid.",
                ),
            )
            return

        try:
            cleanup_result = cleanup_legacy_mailbox_secret_v1(
                resolved_user["email"],
                inbox["id"],
                use_same_credentials,
            )
        except Exception:
            _send_json(
                self,
                503,
                _error(
                    "mailbox_secret_store_unavailable",
                    "Mailbox secret storage is temporarily unavailable.",
                ),
            )
            return

        cleanup_status = cleanup_result.get("status")
        if cleanup_status in {"deleted", "already_absent"}:
            _send_json(self, 200, {"ok": True, "status": cleanup_status})
            return

        status_code, error_code, message = CLEANUP_FAILURES.get(
            cleanup_status,
            (
                503,
                "mailbox_secret_store_unavailable",
                "Mailbox secret storage is temporarily unavailable.",
            ),
        )
        _send_json(self, status_code, _error(error_code, message))

    def do_GET(self):
        _send_method_not_allowed(self)

    def do_PUT(self):
        _send_method_not_allowed(self)

    def do_PATCH(self):
        _send_method_not_allowed(self)

    def do_DELETE(self):
        _send_method_not_allowed(self)

    def do_HEAD(self):
        _send_method_not_allowed(self, write_body=False)

    def do_OPTIONS(self):
        _send_method_not_allowed(self)

    def log_message(self, format, *args):
        return
