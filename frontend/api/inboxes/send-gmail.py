import base64
import binascii
import json
import smtplib
import sys
from email.message import EmailMessage
from email.utils import getaddresses
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from authenticated_imap import (
    find_forbidden_custom_request_fields,
    resolve_authenticated_imap_mailbox,
)
from authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES,
    MAX_SEND_REQUEST_BODY_BYTES,
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
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_ATTACHMENTS = 10
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_RECIPIENTS = 100
MAX_SUBJECT_CHARACTERS = 998
MAX_BODY_CHARACTERS = 2 * 1024 * 1024


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _split_recipients(value: str):
    parsed = []

    for _, address in getaddresses([value or ""]):
        normalized = address.strip()
        if normalized:
            parsed.append(normalized)

    return parsed


def _has_unsafe_header_chars(value: str):
    return "\r" in value or "\n" in value


def _is_valid_address(value: str):
    return bool(value) and "@" in value and not _has_unsafe_header_chars(value)


def _is_safe_auth_value(value: str):
    return bool(value) and not _has_unsafe_header_chars(value)


def _gmail_api_send(access_token: str, message: EmailMessage) -> tuple[dict | None, dict | None]:
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/send",
        data=json.dumps({"raw": encoded_message}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
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
        return None, {"code": gmail_http_error_code(error.code, "gmail_send_failed")}
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _send_with_gmail_oauth(context: dict, message: EmailMessage) -> tuple[bool, dict | None, dict | None]:
    _, send_error = _gmail_api_send(context["access_token"], message)
    if send_error and send_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return False, None, refreshed
        context = refreshed["context"]
        _, send_error = _gmail_api_send(context["access_token"], message)
    return send_error is None, send_error, None


def _build_message(payload: dict, *, require_password: bool = True):
    provider = str(payload.get("provider", "")).strip().lower()
    mailbox_email = str(payload.get("email", "")).strip()
    username = str(payload.get("username", "")).strip() or mailbox_email
    password = str(payload.get("password", ""))
    from_address = mailbox_email
    to_value = str(payload.get("to", "")).strip()
    cc_value = str(payload.get("cc", "")).strip()
    bcc_value = str(payload.get("bcc", "")).strip()
    subject = str(payload.get("subject", "")).strip() or "Untitled message"
    body_html = str(payload.get("bodyHtml", ""))
    body_text = str(payload.get("bodyText", "")).strip() or " "
    attachments = payload.get("attachments") or []

    if provider not in {"google", "custom_imap"}:
        raise ValueError("Only Gmail and custom SMTP sending are supported by this endpoint.")
    if not mailbox_email or not username or (require_password and not password):
        raise ValueError("Missing sending credentials for this mailbox.")
    if not _is_valid_address(mailbox_email):
        raise ValueError("Mailbox credentials are invalid.")
    if provider == "google" and not _is_valid_address(username):
        raise ValueError("Mailbox credentials are invalid.")
    if provider == "custom_imap" and not _is_safe_auth_value(username):
        raise ValueError("Mailbox credentials are invalid.")
    if provider == "google" and mailbox_email.strip().lower() != username.strip().lower():
        raise ValueError("Gmail username must match the connected mailbox email.")
    if _has_unsafe_header_chars(subject):
        raise ValueError("Subject is invalid.")
    if any(_has_unsafe_header_chars(value) for value in (to_value, cc_value, bcc_value)):
        raise ValueError("Recipient headers are invalid.")
    if not isinstance(attachments, list):
        raise ValueError("Attachments payload is invalid.")
    if len(subject) > MAX_SUBJECT_CHARACTERS:
        raise ValueError("Subject is too long.")
    if len(body_html) > MAX_BODY_CHARACTERS or len(body_text) > MAX_BODY_CHARACTERS:
        raise ValueError("Message body is too large.")
    if len(attachments) > MAX_ATTACHMENTS:
        raise ValueError("Too many attachments.")

    to_recipients = _split_recipients(to_value)
    cc_recipients = _split_recipients(cc_value)
    bcc_recipients = _split_recipients(bcc_value)
    all_recipients = [*to_recipients, *cc_recipients, *bcc_recipients]

    if not all_recipients:
        raise ValueError("Add at least one recipient before sending.")
    if len(all_recipients) > MAX_RECIPIENTS:
        raise ValueError("Too many recipients.")
    if not all(_is_valid_address(address) for address in all_recipients):
        raise ValueError("One or more recipient addresses are invalid.")

    message = EmailMessage()
    message["From"] = from_address
    if to_recipients:
        message["To"] = ", ".join(to_recipients)
    if cc_recipients:
        message["Cc"] = ", ".join(cc_recipients)
    message["Subject"] = subject
    message.set_content(body_text)

    if body_html.strip():
        message.add_alternative(body_html, subtype="html")

    total_attachment_bytes = 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Attachment payload is invalid.")
        name = str((attachment or {}).get("name", "")).strip()
        mime_type = str((attachment or {}).get("mimeType", "")).strip() or "application/octet-stream"
        content_base64 = str((attachment or {}).get("contentBase64", "")).strip()

        if (
            not name
            or len(name) > 255
            or not content_base64
            or _has_unsafe_header_chars(name)
            or _has_unsafe_header_chars(mime_type)
        ):
            raise ValueError("Attachment payload is invalid.")

        maintype, _, subtype = mime_type.partition("/")
        if not maintype or not subtype:
            maintype = "application"
            subtype = "octet-stream"

        try:
            content_bytes = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Attachment content could not be decoded.") from exc
        total_attachment_bytes += len(content_bytes)
        if total_attachment_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("Attachments are too large.")

        message.add_attachment(
            content_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=name,
        )

    return username, password, all_recipients, message


def _build_custom_smtp_config(payload: dict):
    host = str(payload.get("smtpHost", "")).strip()
    raw_port = str(payload.get("smtpPort", "")).strip()
    security = str(payload.get("smtpSecurity", "")).strip().lower()

    if not host or not raw_port:
        raise ValueError("SMTP host and port are required for this mailbox.")
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP security must be SSL/TLS or STARTTLS.")
    if _has_unsafe_header_chars(host) or any(char.isspace() for char in host):
        raise ValueError("SMTP host is invalid.")

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("SMTP port is invalid.") from exc

    if port < 1 or port > 65535:
        raise ValueError("SMTP port is invalid.")

    return host, port, security


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for mailbox sending.",
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
                error_payload("internal_error", "The email could not be sent."),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self, max_bytes=MAX_SEND_REQUEST_BODY_BYTES)
        if request_error:
            send_json(self, 413 if request_error["error"]["code"] == "request_too_large" else 400, request_error)
            return

        allowed_fields = {
            "mailboxId", "to", "cc", "bcc", "subject", "bodyHtml", "bodyText", "attachments",
        }
        field_error = reject_unknown_fields(payload, allowed_fields)
        if field_error or find_forbidden_custom_request_fields(payload):
            send_json(
                self,
                400,
                error_payload("forbidden_connection_fields", "Connection and identity details are not accepted."),
            )
            return

        owned = resolve_owned_mailbox(self.headers, payload.get("mailboxId"))
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")

        if provider == "google":
            gmail = resolve_gmail_context(owned)
            if gmail["status"] != "ok":
                send_json(self, gmail["status_code"], gmail["error"])
                return
            context = gmail["context"]
            internal_payload = {
                **payload,
                "provider": "google",
                "email": context["mailbox_email"],
                "username": context["mailbox_email"],
                "password": "",
            }
            try:
                _, _, _, message = _build_message(internal_payload, require_password=False)
            except ValueError as error:
                send_json(self, 400, error_payload("invalid_request", str(error)))
                return

            sent, send_error, refresh_failure = _send_with_gmail_oauth(context, message)
            if refresh_failure:
                send_json(self, refresh_failure["status_code"], refresh_failure["error"])
                return
            if not sent:
                code = (send_error or {}).get("code")
                if code == "gmail_token_invalid":
                    send_json(self, 401, error_payload("reconnect_required", "Reconnect this Gmail inbox to continue."))
                elif code == "gmail_permission_denied":
                    send_json(self, 403, error_payload("gmail_permission_denied", "Gmail did not permit this operation."))
                elif code == "gmail_rate_limited":
                    send_json(self, 502, error_payload("gmail_rate_limited", "Gmail is temporarily rate limited."))
                elif code == "gmail_unavailable":
                    send_json(self, 502, error_payload("gmail_unavailable", "Gmail is temporarily unavailable."))
                else:
                    send_json(self, 502, error_payload("gmail_send_failed", "Gmail could not send this message."))
                return
            send_json(self, 200, {"ok": True})
            return

        if provider != "custom_imap":
            send_json(self, 400, error_payload("unsupported_provider", "Sending is not available for this mailbox."))
            return

        resolved = resolve_authenticated_imap_mailbox(
            self.headers,
            payload.get("mailboxId"),
            require_smtp=True,
        )
        if resolved["status"] != "ok" or not resolved["mailbox"]:
            error = resolved["error"] or {
                "code": "mailbox_configuration_malformed",
                "message": "Mailbox configuration is invalid.",
                "status_code": 500,
            }
            send_json(self, error["status_code"], error_payload(error["code"], error["message"]))
            return
        mailbox = resolved["mailbox"]
        internal_payload = {
            **payload,
            "provider": "custom_imap",
            "email": mailbox["email"],
            "username": mailbox["smtp"]["username"],
            "password": mailbox["smtp"]["password"],
            "smtpHost": mailbox["smtp"]["host"],
            "smtpPort": str(mailbox["smtp"]["port"]),
            "smtpSecurity": mailbox["smtp"]["security"],
        }
        try:
            username, password, recipients, message = _build_message(internal_payload)
            smtp_host, smtp_port, smtp_security = _build_custom_smtp_config(internal_payload)
        except ValueError as error:
            send_json(self, 400, error_payload("invalid_request", str(error)))
            return

        try:
            if smtp_security == "ssl":
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as smtp:
                    smtp.login(username, password)
                    smtp.send_message(message, to_addrs=recipients)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
                    smtp.starttls()
                    smtp.login(username, password)
                    smtp.send_message(message, to_addrs=recipients)
        except smtplib.SMTPAuthenticationError:
            send_json(self, 401, error_payload("invalid_credentials", "Stored SMTP credentials were rejected."))
            return
        except smtplib.SMTPException:
            send_json(self, 502, error_payload("send_failed", "SMTP could not send this message."))
            return
        except Exception:
            send_json(self, 500, error_payload("internal_error", "Could not send email."))
            return
        send_json(self, 200, {"ok": True})

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for mailbox sending.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST for mailbox sending.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
