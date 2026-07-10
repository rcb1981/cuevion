import imaplib
import json
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from beta_auth import parse_beta_session_token, read_beta_session_cookie  # noqa: E402
from imap_connect_preview import connect_mailbox_with_settings  # noqa: E402
from mailbox_secret_store import get_mailbox_secret  # noqa: E402
from oauth_token_store import (  # noqa: E402
    get_google_token_record_with_metadata,
    refresh_google_token_record,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SUPPORTED_ACTIONS = {"mark_read", "mark_unread", "flag", "unflag"}
GMAIL_ACTION_LABELS = {
    "mark_read": {"removeLabelIds": ["UNREAD"]},
    "mark_unread": {"addLabelIds": ["UNREAD"]},
    "flag": {"addLabelIds": ["STARRED"]},
    "unflag": {"removeLabelIds": ["STARRED"]},
}
IMAP_ACTION_FLAGS = {
    "mark_read": ("+FLAGS.SILENT", "\\Seen"),
    "mark_unread": ("-FLAGS.SILENT", "\\Seen"),
    "flag": ("+FLAGS.SILENT", "\\Flagged"),
    "unflag": ("-FLAGS.SILENT", "\\Flagged"),
}


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _get_authenticated_user(headers) -> dict | None:
    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _has_unsafe_auth_chars(value: str) -> bool:
    return "\r" in value or "\n" in value


def _is_token_expired(token_record: dict) -> bool:
    expires_at = token_record.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False

    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    return parsed <= datetime.now(timezone.utc)


def _token_record_has_known_modify_scope(token_record: dict) -> bool | None:
    scope_value = token_record.get("scope")
    if not isinstance(scope_value, str) or not scope_value.strip():
        return None

    scopes = set(scope_value.split())
    return GMAIL_MODIFY_SCOPE in scopes or "https://mail.google.com/" in scopes


def _gmail_modify_request(
    access_token: str,
    message_id: str,
    action: str,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/{quote(message_id, safe='')}/modify",
        data=json.dumps(GMAIL_ACTION_LABELS[action]).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
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

        gmail_error = parsed_error.get("error") if isinstance(parsed_error, dict) else None
        error_message = (
            gmail_error.get("message")
            if isinstance(gmail_error, dict)
            else None
        ) or f"Gmail mailbox action failed with HTTP {error.code}."
        status = (
            str(gmail_error.get("status") or "").strip().lower()
            if isinstance(gmail_error, dict)
            else ""
        )

        error_code = "gmail_message_action_failed"
        if error.code == 401:
            error_code = "gmail_token_invalid"
        elif error.code == 403:
            error_code = "REAUTH_REQUIRED"
            if (
                "insufficient" in error_message.lower()
                or "scope" in error_message.lower()
                or status == "permission_denied"
            ):
                error_code = "MISSING_GMAIL_MODIFY_SCOPE"

        return None, {
            "code": error_code,
            "message": error_message,
            "status_code": error.code,
        }
    except URLError as error:
        return None, {
            "code": "gmail_unavailable",
            "message": (
                str(error.reason)
                if getattr(error, "reason", None)
                else "Could not reach Gmail."
            ),
        }


def _perform_gmail_action(handler: BaseHTTPRequestHandler, payload: dict, action: str):
    email_address = str(payload.get("email") or "").strip().lower()
    message_id = str(payload.get("messageId") or payload.get("providerMessageId") or "").strip()

    if not email_address or not message_id:
        _json_response(
            handler,
            400,
            _error("invalid_request", "Gmail mailbox actions require email and message id."),
        )
        return

    token_record = get_google_token_record_with_metadata(email_address)
    if not token_record:
        _json_response(
            handler,
            401,
            _error("gmail_token_missing", "No stored Gmail token is available for this mailbox."),
        )
        return

    known_modify_scope = _token_record_has_known_modify_scope(token_record)
    if known_modify_scope is False:
        _json_response(
            handler,
            403,
            _error(
                "MISSING_GMAIL_MODIFY_SCOPE",
                "Reconnect this Gmail inbox to enable read/unread and star actions.",
            ),
        )
        return

    access_token = token_record.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        _json_response(
            handler,
            401,
            _error("gmail_token_missing", "The stored Gmail token record is incomplete."),
        )
        return

    if _is_token_expired(token_record):
        refreshed_record, refresh_error = refresh_google_token_record(email_address)
        if refresh_error:
            _json_response(handler, 401, _error(refresh_error["code"], refresh_error["message"]))
            return

        token_record = refreshed_record or token_record
        access_token = token_record.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            _json_response(
                handler,
                401,
                _error("gmail_token_missing", "The refreshed Gmail token record is incomplete."),
            )
            return

    _, modify_error = _gmail_modify_request(access_token.strip(), message_id, action)
    if modify_error and modify_error.get("code") == "gmail_token_invalid":
        refreshed_record, refresh_error = refresh_google_token_record(email_address)
        if not refresh_error and isinstance(refreshed_record, dict):
            refreshed_token = refreshed_record.get("access_token")
            if isinstance(refreshed_token, str) and refreshed_token.strip():
                _, modify_error = _gmail_modify_request(
                    refreshed_token.strip(),
                    message_id,
                    action,
                )

    if modify_error:
        status_code = 502
        if modify_error.get("code") in {"REAUTH_REQUIRED", "MISSING_GMAIL_MODIFY_SCOPE"}:
            status_code = 403
        elif modify_error.get("code") == "gmail_token_invalid":
            status_code = 401
        _json_response(
            handler,
            status_code,
            _error(modify_error["code"], modify_error["message"]),
        )
        return

    _json_response(handler, 200, {"ok": True, "action": action})


def _read_uid_validity(mailbox, folder: str) -> str | None:
    status, data = mailbox.status(folder, "(UIDVALIDITY)")
    if status != "OK" or not data or not data[0]:
        return None

    metadata = data[0].decode("utf-8", errors="ignore") if isinstance(data[0], bytes) else str(data[0])
    match = re.search(r"UIDVALIDITY\s+(\d+)", metadata)
    return match.group(1) if match else None


def _perform_imap_action(handler: BaseHTTPRequestHandler, payload: dict, action: str):
    session_user = _get_authenticated_user(handler.headers)
    email_address = str(payload.get("email") or "").strip()
    host = str(payload.get("host") or "").strip()
    raw_port = str(payload.get("port") or "").strip()
    ssl_enabled = bool(payload.get("ssl", True))
    username = str(payload.get("username") or "").strip() or email_address
    password = str(payload.get("password") or "")
    mailbox_id = str(payload.get("mailboxId") or "").strip()
    folder = str(payload.get("folder") or "").strip()
    uid = str(payload.get("uid") or payload.get("imapUid") or "").strip()
    uid_validity = str(payload.get("uidValidity") or "").strip() or None

    try:
        port = int(raw_port)
    except ValueError:
        port = 0

    if not password and session_user and mailbox_id:
        secret_record = get_mailbox_secret(session_user["email"], mailbox_id)
        stored_imap_password = (
            secret_record.get("imapPassword")
            if isinstance(secret_record, dict)
            else None
        )
        if isinstance(stored_imap_password, str) and stored_imap_password:
            password = stored_imap_password

    if not folder:
        _json_response(
            handler,
            400,
            _error("missing_folder", "IMAP mailbox actions require the source folder."),
        )
        return

    if not uid:
        _json_response(
            handler,
            400,
            _error("missing_imap_uid", "IMAP mailbox actions require the message UID."),
        )
        return

    if (
        not email_address
        or not host
        or port <= 0
        or not username
        or _has_unsafe_auth_chars(username)
        or not password
    ):
        _json_response(
            handler,
            400,
            _error("invalid_request", "IMAP mailbox credentials and message metadata are required."),
        )
        return

    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            host=host,
            port=port,
            username=username,
            password=password,
            ssl_enabled=ssl_enabled,
        )

        select_status, _ = mailbox.select(folder)
        if select_status != "OK":
            _json_response(
                handler,
                404,
                _error("mailbox_not_found", "The source mailbox folder could not be opened."),
            )
            return

        if uid_validity:
            current_uid_validity = _read_uid_validity(mailbox, folder)
            if current_uid_validity and current_uid_validity != uid_validity:
                _json_response(
                    handler,
                    409,
                    _error("uid_validity_changed", "This mailbox changed since the message was fetched."),
                )
                return

        operation, flag = IMAP_ACTION_FLAGS[action]
        status, _ = mailbox.uid("store", uid, operation, f"({flag})")
        if status != "OK":
            _json_response(
                handler,
                404,
                _error("message_action_failed", "The source message could not be updated."),
            )
            return

        _json_response(handler, 200, {"ok": True, "action": action})
    except imaplib.IMAP4.error as exc:
        _json_response(
            handler,
            401,
            _error("invalid_credentials", str(exc) or "IMAP credentials were rejected."),
        )
    except Exception:
        _json_response(
            handler,
            502,
            _error("imap_message_action_failed", "Could not update this message through IMAP."),
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload, error = _read_json_body(self)
        if error:
            _json_response(self, 400, error)
            return

        action = str((payload or {}).get("action") or "").strip()
        provider = str((payload or {}).get("provider") or "").strip().lower()

        if action not in SUPPORTED_ACTIONS:
            _json_response(
                self,
                400,
                _error("unsupported_action", "This mailbox action is not supported."),
            )
            return

        if provider in {"gmail", "google"}:
            _perform_gmail_action(self, payload or {}, action)
            return

        if provider in {"imap", "custom_imap"}:
            _perform_imap_action(self, payload or {}, action)
            return

        _json_response(
            self,
            400,
            _error("unsupported_provider", "Mailbox actions require Gmail or IMAP."),
        )

    def do_GET(self):
        _json_response(
            self,
            405,
            _error("method_not_allowed", "Use POST for mailbox message actions."),
        )

    def log_message(self, format, *args):
        return
