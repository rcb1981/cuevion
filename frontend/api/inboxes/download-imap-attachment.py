import imaplib
import json
import re
import sys
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

from imap_connect_preview import (
    connect_mailbox_with_settings,
    get_message_attachment_payload,
)


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


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


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, _error("invalid_request", "Request body must be valid JSON."))
            return

        provider = str(payload.get("provider") or "").strip().lower()
        email_address = str(payload.get("email") or "").strip()
        host = str(payload.get("host") or "").strip()
        raw_port = str(payload.get("port") or "").strip()
        ssl_enabled = bool(payload.get("ssl", True))
        username = str(payload.get("username") or "").strip() or email_address
        password = str(payload.get("password") or "")
        folder = str(payload.get("folder") or "INBOX").strip() or "INBOX"
        uid = str(payload.get("uid") or "").strip()
        uid_validity = str(payload.get("uidValidity") or "").strip() or None
        attachment_id = str(payload.get("attachmentId") or "").strip()

        if provider != "custom_imap":
            _json_response(
                self,
                400,
                _error("unsupported_provider", "Only custom IMAP attachment downloads are supported here."),
            )
            return

        try:
            port = int(raw_port)
        except ValueError:
            port = 0

        if (
            not email_address
            or not host
            or port <= 0
            or not _safe_auth_value(username)
            or not password
            or not uid
            or not attachment_id
        ):
            _json_response(
                self,
                400,
                _error("invalid_request", "Mailbox credentials, UID, and attachment id are required."),
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

            select_status, _ = mailbox.select(folder, readonly=True)
            if select_status != "OK":
                _json_response(
                    self,
                    404,
                    _error("mailbox_not_found", "The source mailbox folder could not be opened."),
                )
                return

            if uid_validity:
                current_uid_validity = _read_uid_validity(mailbox, folder)
                if current_uid_validity and current_uid_validity != uid_validity:
                    _json_response(
                        self,
                        409,
                        _error("uid_validity_changed", "This mailbox changed since the message was fetched."),
                    )
                    return

            status, message_data = mailbox.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                _json_response(
                    self,
                    404,
                    _error("message_not_found", "The source message could not be found."),
                )
                return

            raw_email = _extract_raw_email(message_data)
            if raw_email is None:
                _json_response(
                    self,
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
                    self,
                    404,
                    _error("attachment_not_found", "The requested attachment could not be found."),
                )
                return

            _binary_response(
                self,
                attachment["content"],
                attachment["filename"],
                attachment["mimeType"],
            )
        except imaplib.IMAP4.error as exc:
            _json_response(
                self,
                401,
                _error("invalid_credentials", str(exc) or "IMAP credentials were rejected."),
            )
        except Exception:
            _json_response(
                self,
                502,
                _error("imap_attachment_download_failed", "Could not download this attachment from IMAP."),
            )
        finally:
            if mailbox is not None:
                try:
                    mailbox.logout()
                except Exception:
                    pass

    def do_GET(self):
        _json_response(
            self,
            405,
            _error("method_not_allowed", "Use POST to download IMAP attachments."),
        )

    def log_message(self, format, *args):
        return
