import base64
import binascii
import imaplib
import json
import re
import sys
from email import message_from_bytes
from http import HTTPStatus
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

from authenticated_imap import (
    find_forbidden_custom_request_fields,
    resolve_authenticated_imap_mailbox,
)
from imap_connect_preview import (
    connect_mailbox_with_settings,
    get_message_attachment_payload,
)
from authenticated_gmail import (
    MAX_GMAIL_RAW_MESSAGE_BYTES,
    error_payload,
    gmail_http_error_code,
    read_bounded_response,
    read_json_body,
    refresh_gmail_context,
    reject_unknown_fields,
    resolve_gmail_context,
    resolve_owned_mailbox,
    send_json,
    send_method_not_allowed,
    valid_identifier,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


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


def _gmail_request(access_token: str, path: str) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=20) as response:
            body = read_bounded_response(response, MAX_GMAIL_RAW_MESSAGE_BYTES)
            if body is None:
                return None, {"code": "gmail_response_too_large"}
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"code": "gmail_response_invalid"}
            if not isinstance(payload, dict):
                return None, {"code": "gmail_response_invalid"}
            return payload, None
    except HTTPError as error:
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_attachment_download_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _safe_auth_value(value: str) -> bool:
    return bool(value) and "\r" not in value and "\n" not in value


def _safe_header_filename(value: str) -> str:
    normalized = re.sub(r"[\r\n\"\\]", "_", value).strip()
    return normalized or "attachment"


def _binary_response(
    handler: BaseHTTPRequestHandler,
    content: bytes,
    filename: str,
    mime_type: str,
):
    safe_filename = _safe_header_filename(filename)
    handler.send_response(200)
    handler.send_header("Content-Type", mime_type or "application/octet-stream")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header(
        "Content-Disposition",
        f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{quote(filename or "attachment", safe="")}',
    )
    handler.end_headers()
    handler.wfile.write(content)


def _extract_raw_email(message_data) -> bytes | None:
    for item in message_data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return None


def _read_uid_validity(mailbox, folder: str) -> str | None:
    status, data = mailbox.status(folder, "(UIDVALIDITY)")
    if status != "OK" or not data or not data[0]:
        return None

    metadata = data[0].decode("utf-8", errors="ignore") if isinstance(data[0], bytes) else str(data[0])
    match = re.search(r"UIDVALIDITY\s+(\d+)", metadata)
    return match.group(1) if match else None


def _download_gmail_attachment(handler: BaseHTTPRequestHandler, payload: dict, context: dict):
    message_id = payload.get("messageId")
    attachment_id = payload.get("attachmentId")
    if not valid_identifier(message_id) or not valid_identifier(attachment_id):
        send_json(handler, 400, error_payload("invalid_request", "Message and attachment ids are invalid."))
        return

    message_payload, message_error = _gmail_request(
        context["access_token"],
        f"/messages/{quote(message_id, safe='')}?format=raw",
    )
    if message_error and message_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            send_json(handler, refreshed["status_code"], refreshed["error"])
            return
        context = refreshed["context"]
        message_payload, message_error = _gmail_request(
            context["access_token"],
            f"/messages/{quote(message_id, safe='')}?format=raw",
        )
    if message_error:
        code = message_error.get("code")
        if code == "gmail_token_invalid":
            send_json(handler, 401, error_payload("reconnect_required", "Reconnect this Gmail inbox to continue."))
        elif code == "gmail_permission_denied":
            send_json(handler, 403, error_payload("gmail_permission_denied", "Gmail did not permit this operation."))
        elif code == "gmail_rate_limited":
            send_json(handler, 502, error_payload("gmail_rate_limited", "Gmail is temporarily rate limited."))
        elif code == "gmail_unavailable":
            send_json(handler, 502, error_payload("gmail_unavailable", "Gmail is temporarily unavailable."))
        elif code in {"gmail_response_invalid", "gmail_response_too_large"}:
            send_json(handler, 502, error_payload(code, "Gmail returned an invalid attachment response."))
        else:
            send_json(handler, 502, error_payload("gmail_attachment_download_failed", "Gmail attachment could not be downloaded."))
        return

    raw_message = message_payload.get("raw") if isinstance(message_payload, dict) else None
    if not isinstance(raw_message, str) or not raw_message:
        send_json(handler, 404, error_payload("attachment_not_found", "The requested attachment could not be found."))
        return
    try:
        parsed_message = message_from_bytes(_base64url_decode(raw_message))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        send_json(handler, 502, error_payload("gmail_response_invalid", "Gmail returned an invalid attachment response."))
        return
    attachment = get_message_attachment_payload(parsed_message, attachment_id)
    if not attachment:
        send_json(handler, 404, error_payload("attachment_not_found", "The requested attachment could not be found."))
        return
    if len(attachment["content"]) > MAX_GMAIL_RAW_MESSAGE_BYTES:
        send_json(handler, 502, error_payload("gmail_response_too_large", "The requested attachment is too large."))
        return
    _binary_response(
        handler,
        attachment["content"],
        attachment["filename"],
        attachment["mimeType"],
    )


def _download_imap_attachment(handler: BaseHTTPRequestHandler, payload: dict):
    if set(payload) - {"mailboxId", "folder", "uid", "uidValidity", "attachmentId"}:
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return
    if find_forbidden_custom_request_fields(payload):
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return

    mailbox_id = payload.get("mailboxId")
    folder = payload.get("folder")
    uid = payload.get("uid")
    uid_validity = payload.get("uidValidity")
    attachment_id = payload.get("attachmentId")

    if (
        not isinstance(folder, str)
        or not folder
        or folder != folder.strip()
        or "\r" in folder
        or "\n" in folder
        or not isinstance(uid, str)
        or not uid.isdigit()
        or uid == "0"
        or (uid_validity is not None and (
            not isinstance(uid_validity, str) or not uid_validity.isdigit()
        ))
        or not isinstance(attachment_id, str)
        or not attachment_id
        or attachment_id != attachment_id.strip()
        or "\r" in attachment_id
        or "\n" in attachment_id
    ):
        _json_response(
            handler,
            400,
            _error("invalid_request", "Mailbox UID and attachment id are required."),
        )
        return

    resolved = resolve_authenticated_imap_mailbox(handler.headers, mailbox_id)
    if resolved["status"] != "ok" or not resolved["mailbox"]:
        error = resolved["error"] or {
            "code": "mailbox_configuration_malformed",
            "message": "Mailbox configuration is invalid.",
            "status_code": 500,
        }
        _json_response(handler, error["status_code"], _error(error["code"], error["message"]))
        return
    imap = resolved["mailbox"]["imap"]

    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            host=imap["host"],
            port=imap["port"],
            username=imap["username"],
            password=imap["password"],
            ssl_enabled=imap["ssl"],
        )

        select_status, _ = mailbox.select(folder, readonly=True)
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

        status, message_data = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK":
            _json_response(
                handler,
                404,
                _error("message_not_found", "The source message could not be found."),
            )
            return

        raw_email = _extract_raw_email(message_data)
        if raw_email is None:
            _json_response(
                handler,
                404,
                _error("message_not_found", "The source message could not be found."),
            )
            return

        attachment = get_message_attachment_payload(
            message_from_bytes(raw_email),
            attachment_id,
        )
        if not attachment:
            _json_response(
                handler,
                404,
                _error("attachment_not_found", "The requested attachment could not be found."),
            )
            return

        _binary_response(
            handler,
            attachment["content"],
            attachment["filename"],
            attachment["mimeType"],
        )
    except imaplib.IMAP4.error:
        _json_response(
            handler,
            401,
            _error("invalid_credentials", "Stored IMAP credentials were rejected."),
        )
    except Exception:
        _json_response(
            handler,
            502,
            _error("imap_attachment_download_failed", "Could not download this attachment from IMAP."),
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST to download attachments.",
                write_body=getattr(self, "command", "") != "HEAD",
            )
            return
        super().send_error(code, message, explain)

    def do_POST(self):
        try:
            handler._handle_post(self)
        except Exception:
            send_json(
                self,
                500,
                error_payload("internal_error", "The attachment request could not be completed."),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(self, 413 if request_error["error"]["code"] == "request_too_large" else 400, request_error)
            return

        field_error = reject_unknown_fields(
            payload,
            {"mailboxId", "messageId", "attachmentId", "folder", "uid", "uidValidity"},
        )
        if field_error or find_forbidden_custom_request_fields(payload):
            send_json(self, 400, error_payload("forbidden_connection_fields", "Connection and identity details are not accepted."))
            return

        owned = resolve_owned_mailbox(self.headers, payload.get("mailboxId"))
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")

        if provider == "google":
            field_error = reject_unknown_fields(payload, {"mailboxId", "messageId", "attachmentId"})
            if field_error:
                send_json(self, 400, field_error)
                return
            gmail = resolve_gmail_context(owned)
            if gmail["status"] != "ok":
                send_json(self, gmail["status_code"], gmail["error"])
                return
            _download_gmail_attachment(self, payload, gmail["context"])
            return

        if provider == "custom_imap":
            _download_imap_attachment(self, payload)
            return

        send_json(self, 400, error_payload("unsupported_provider", "Attachment download is not available for this mailbox."))

    def do_GET(self):
        send_method_not_allowed(self, "Use POST to download attachments.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST to download attachments.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
