import json
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from authenticated_imap import resolve_authenticated_imap_mailbox  # noqa: E402
from mailbox_secret_store import (  # noqa: E402
    restore_encrypted_mailbox_secret_snapshot,
    save_mailbox_secret,
    snapshot_encrypted_mailbox_secret,
)
from user_config_store import (  # noqa: E402
    resolve_authenticated_user,
    resolve_owned_managed_inbox_record,
    upsert_owned_custom_imap_mailbox,
)

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
SMTP_FIELDS = {
    "host",
    "port",
    "security",
    "username",
    "password",
    "useSameCredentials",
}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


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


def _resolved_config_contains_mailbox_id(result: dict, mailbox_id: str) -> bool:
    config = result.get("config")
    managed_inboxes = config.get("managedInboxes") if isinstance(config, dict) else None
    return isinstance(managed_inboxes, list) and any(
        isinstance(inbox, dict) and inbox.get("id") == mailbox_id
        for inbox in managed_inboxes
    )


def _parse_credential_connection(payload: dict) -> tuple[dict | None, dict | None]:
    if not _has_only_fields(payload, INITIAL_FIELDS):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")
    mailbox_id = _valid_mailbox_id(payload.get("mailboxId"))
    connection = payload.get("connection")
    if not mailbox_id or not _has_only_fields(connection, CONNECTION_FIELDS):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")
    imap = connection.get("imap")
    smtp = connection.get("smtp")
    if not _has_only_fields(imap, IMAP_FIELDS) or not _has_only_fields(smtp, SMTP_FIELDS):
        return None, _error("invalid_request", "Mailbox connection request is invalid.")

    email = _exact_string(connection.get("email"))
    imap_host = _exact_string(imap.get("host"))
    imap_port = _port(imap.get("port"))
    imap_username = _exact_string(imap.get("username"))
    imap_password = _exact_string(imap.get("password"))
    smtp_host = _exact_string(smtp.get("host"))
    smtp_port = _port(smtp.get("port"))
    smtp_username = _exact_string(smtp.get("username"), allow_empty=True)
    smtp_password = _exact_string(smtp.get("password"), allow_empty=True)
    smtp_security = smtp.get("security")
    use_same_credentials = smtp.get("useSameCredentials") is True
    if use_same_credentials:
        smtp_username = imap_username
    if (
        connection.get("provider") != "custom_imap"
        or not email
        or "@" not in email
        or not imap_host
        or not imap_port
        or not imap_username
        or not imap_password
        or not isinstance(imap.get("ssl"), bool)
        or not smtp_host
        or not smtp_port
        or not smtp_username
        or smtp_security not in {"ssl", "starttls"}
        or smtp_password is None
        or (not use_same_credentials and not smtp_password)
    ):
        return None, _error("invalid_request", "Complete mailbox connection details are required.")

    limit = payload.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 100:
        return None, _error("invalid_request", "Connection limit is invalid.")

    return {
        "mode": payload.get("mode"),
        "mailboxId": mailbox_id,
        "email": email,
        "imap": {
            "host": imap_host,
            "port": imap_port,
            "ssl": imap["ssl"],
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
        "limit": limit,
        "internalRole": payload.get("internalRole"),
        "focusPreferences": payload.get("focusPreferences"),
    }, None


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

        mode = payload.get("mode")
        if mode == "refresh":
            self._handle_refresh(payload)
            return
        if mode in {"initial", "reconnect"}:
            self._handle_credential_connection(payload, session_user, mode)
            return
        self._send_json(400, _error("invalid_request", "Connection mode is required."))

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

        preview_request = {
            "mailboxId": parsed["mailboxId"],
            "provider": "custom_imap",
            "email": parsed["email"],
            "host": parsed["imap"]["host"],
            "port": str(parsed["imap"]["port"]),
            "ssl": parsed["imap"]["ssl"],
            "username": parsed["imap"]["username"],
            "password": parsed["imap"]["password"],
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
            self._send_json(502, _error("connection_failed", "Could not connect to inbox."))
            return

        previous_secret = snapshot_encrypted_mailbox_secret(
            session_user["email"],
            parsed["mailboxId"],
        )
        if previous_secret["status"] not in {"present", "missing"}:
            self._send_json(
                503,
                _error(
                    "mailbox_secret_store_unavailable",
                    "Mailbox credential state could not be prepared.",
                ),
            )
            return

        saved_secret, save_error = save_mailbox_secret(
            session_user["email"],
            parsed["mailboxId"],
            imap_password=parsed["imap"]["password"],
            smtp_password=(
                parsed["imap"]["password"]
                if parsed["smtp"]["useSameCredentials"]
                else parsed["smtp"]["password"]
            ),
        )
        if save_error or not saved_secret:
            self._send_json(
                503,
                _error("mailbox_secret_store_unavailable", "Mailbox credentials could not be stored."),
            )
            return

        try:
            upsert_result = upsert_owned_custom_imap_mailbox(
                self.headers,
                parsed["mailboxId"],
                mode,
                {
                    "email": parsed["email"],
                    "customImap": {
                        "host": parsed["imap"]["host"],
                        "port": str(parsed["imap"]["port"]),
                        "ssl": parsed["imap"]["ssl"],
                        "username": parsed["imap"]["username"],
                    },
                    "customSmtp": {
                        "host": parsed["smtp"]["host"],
                        "port": str(parsed["smtp"]["port"]),
                        "security": parsed["smtp"]["security"],
                        "username": parsed["smtp"]["username"],
                        "useSameCredentials": parsed["smtp"]["useSameCredentials"],
                    },
                },
                {
                    key: payload[key]
                    for key in ("internalRole", "focusPreferences")
                    if key in payload
                },
            )
        except Exception:
            upsert_result = {"status": "unavailable", "error": None}
        if upsert_result["status"] != "ok":
            try:
                rollback_error = restore_encrypted_mailbox_secret_snapshot(
                    session_user["email"],
                    parsed["mailboxId"],
                    previous_secret,
                )
            except Exception:
                rollback_error = {
                    "code": "mailbox_secret_store_unavailable",
                    "message": "Mailbox connection state could not be restored safely.",
                }
            if rollback_error:
                self._send_json(
                    503,
                    _error(
                        "mailbox_connection_rollback_failed",
                        "Mailbox connection state could not be restored safely.",
                    ),
                )
                return
            if upsert_result["status"] == "conflict":
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
            if upsert_result["status"] == "not_found":
                self._send_json(
                    404,
                    _error("reconnect_target_not_found", "The mailbox to reconnect was not found."),
                )
                return
            self._send_json(
                503,
                _error("user_config_store_unavailable", "Mailbox configuration could not be stored."),
            )
            return

        self._send_json(200, _preview_success_payload(response_payload))

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
        preview_request = {
            "mailboxId": mailbox_id,
            "provider": "custom_imap",
            "email": mailbox["email"],
            "host": mailbox["imap"]["host"],
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
            self._send_json(502, _error("connection_failed", "Could not refresh this inbox."))
            return
        self._send_json(200, _preview_success_payload(response_payload))

    def do_GET(self):
        self._send_json(405, _error("method_not_allowed", "Use POST for inbox connection."))

    def log_message(self, format, *args):
        return
