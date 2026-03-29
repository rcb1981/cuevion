import base64
import json
import smtplib
from email.message import EmailMessage
from email.utils import getaddresses
from http.server import BaseHTTPRequestHandler


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
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


def _build_message(payload: dict):
    provider = str(payload.get("provider", "")).strip().lower()
    mailbox_email = str(payload.get("email", "")).strip()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    from_address = mailbox_email
    to_value = str(payload.get("to", "")).strip()
    cc_value = str(payload.get("cc", "")).strip()
    bcc_value = str(payload.get("bcc", "")).strip()
    subject = str(payload.get("subject", "")).strip() or "Untitled message"
    body_html = str(payload.get("bodyHtml", ""))
    body_text = str(payload.get("bodyText", "")).strip() or " "
    attachments = payload.get("attachments") or []

    if provider != "google":
        raise ValueError("Only Gmail sending is supported by this endpoint.")
    if not mailbox_email or not username or not password:
        raise ValueError("Missing Gmail credentials for this mailbox.")
    if not _is_valid_address(mailbox_email) or not _is_valid_address(username):
        raise ValueError("Mailbox credentials are invalid.")
    if mailbox_email.strip().lower() != username.strip().lower():
        raise ValueError("Gmail username must match the connected mailbox email.")
    if _has_unsafe_header_chars(subject):
        raise ValueError("Subject is invalid.")
    if not isinstance(attachments, list):
        raise ValueError("Attachments payload is invalid.")

    to_recipients = _split_recipients(to_value)
    cc_recipients = _split_recipients(cc_value)
    bcc_recipients = _split_recipients(bcc_value)
    all_recipients = [*to_recipients, *cc_recipients, *bcc_recipients]

    if not all_recipients:
        raise ValueError("Add at least one recipient before sending.")
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

    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Attachment payload is invalid.")
        name = str((attachment or {}).get("name", "")).strip()
        mime_type = str((attachment or {}).get("mimeType", "")).strip() or "application/octet-stream"
        content_base64 = str((attachment or {}).get("contentBase64", "")).strip()

        if not name or not content_base64 or _has_unsafe_header_chars(name):
            raise ValueError("Attachment payload is invalid.")

        maintype, _, subtype = mime_type.partition("/")
        if not maintype or not subtype:
            maintype = "application"
            subtype = "octet-stream"

        try:
            content_bytes = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError(f"Attachment {name} could not be decoded.") from exc

        message.add_attachment(
            content_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=name,
        )

    return username, password, all_recipients, message


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            _json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": "Request body must be valid JSON.",
                    },
                },
            )
            return

        try:
            username, password, recipients, message = _build_message(payload)
        except ValueError as exc:
            _json_response(
                self,
                400,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": str(exc),
                    },
                },
            )
            return

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
                smtp.login(username, password)
                smtp.send_message(message, to_addrs=recipients)
        except smtplib.SMTPAuthenticationError:
            _json_response(
                self,
                401,
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_credentials",
                        "message": "Gmail rejected the username or app password.",
                    },
                },
            )
            return
        except smtplib.SMTPException:
            _json_response(
                self,
                502,
                {
                    "ok": False,
                    "error": {
                        "code": "send_failed",
                        "message": "Gmail could not send this message.",
                    },
                },
            )
            return
        except Exception:
            _json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "server_error",
                        "message": "Could not send email.",
                    },
                },
            )
            return

        _json_response(self, 200, {"ok": True})

    def do_GET(self):
        _json_response(
            self,
            405,
            {
                "ok": False,
                "error": {
                    "code": "method_not_allowed",
                    "message": "Use POST for Gmail sending.",
                },
            },
        )

    def log_message(self, format, *args):
        return
