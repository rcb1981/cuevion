from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from beta_auth import parse_beta_session_token, read_beta_session_cookie, resolve_beta_session_secret  # noqa: E402

MAX_SUBJECT_LENGTH = 160
MAX_MESSAGE_LENGTH = 5000
MAX_IDENTITY_LENGTH = 254
MAX_WORKSPACE_NAME_LENGTH = 160
MAX_TOPIC_LENGTH = 40
MAX_REQUEST_ID_LENGTH = 80
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
SUPPORT_TOPICS = {
    "General",
    "Inboxes",
    "Learning",
    "Settings",
    "Smart Folders",
    "Message view",
    "Bug",
    "Feedback",
}


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _build_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    try:
        content_length = int(handler.headers.get("content-length", "0"))
    except ValueError:
        return None, _build_error("invalid_request", "Request body is invalid.")

    if content_length > 16_384:
        return None, _build_error("invalid_request", "Request body is too large.")

    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _has_unsafe_header_chars(value: str) -> bool:
    return "\r" in value or "\n" in value


def _is_valid_email(value: str) -> bool:
    return bool(value) and not _has_unsafe_header_chars(value) and EMAIL_PATTERN.match(value) is not None


def _clean_text(value: object, limit: int) -> str:
    normalized = str(value or "").replace("\x00", "").strip()
    if len(normalized) > limit:
        normalized = normalized[:limit].rstrip()
    return normalized


def _normalize_created_at(value: object) -> str:
    raw_value = _clean_text(value, 80)
    if not raw_value:
        return datetime.now(timezone.utc).isoformat()

    try:
        datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).isoformat()

    return raw_value


def _resolve_authenticated_user(headers) -> dict | None:
    session_secret = resolve_beta_session_secret()
    if not session_secret:
        return None

    session_token = read_beta_session_cookie(headers)
    return parse_beta_session_token(session_token or "")


def _resolve_smtp_config() -> tuple[dict | None, dict | None]:
    to_email = os.getenv("CUEVION_SUPPORT_TO_EMAIL", "").strip()
    from_email = os.getenv("CUEVION_SUPPORT_FROM_EMAIL", "").strip()
    host = os.getenv("SMTP_HOST", "").strip()
    raw_port = os.getenv("SMTP_PORT", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    raw_secure = os.getenv("SMTP_SECURE", "").strip().lower()

    if not all([to_email, from_email, host, raw_port, username, password]):
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    if not _is_valid_email(to_email) or not _is_valid_email(from_email):
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    if _has_unsafe_header_chars(host) or any(char.isspace() for char in host):
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    try:
        port = int(raw_port)
    except ValueError:
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    if port < 1 or port > 65535:
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    if raw_secure in {"true", "1", "yes", "ssl", "tls"}:
        secure = "ssl"
    elif raw_secure in {"false", "0", "no", "starttls"}:
        secure = "starttls"
    elif raw_secure == "":
        secure = "ssl" if port == 465 else "starttls"
    else:
        return None, _build_error("support_not_configured", "Support delivery is not configured.")

    return {
        "to_email": to_email,
        "from_email": from_email,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "secure": secure,
    }, None


def _normalize_support_payload(payload: dict) -> tuple[dict | None, dict | None]:
    subject = _clean_text(payload.get("subject"), MAX_SUBJECT_LENGTH)
    message = _clean_text(payload.get("message"), MAX_MESSAGE_LENGTH)
    request_id = _clean_text(payload.get("id"), MAX_REQUEST_ID_LENGTH)
    submitted_by = _clean_text(payload.get("submittedBy"), MAX_IDENTITY_LENGTH) or "Beta tester"
    workspace_name = _clean_text(payload.get("workspaceName"), MAX_WORKSPACE_NAME_LENGTH) or "Workspace"
    topic = _clean_text(payload.get("topic"), MAX_TOPIC_LENGTH) or "General"
    created_at = _normalize_created_at(payload.get("createdAt"))

    if not subject:
        return None, _build_error("invalid_request", "Subject is required.")
    if not message:
        return None, _build_error("invalid_request", "Message is required.")
    if _has_unsafe_header_chars(subject):
        return None, _build_error("invalid_request", "Subject is invalid.")
    if topic not in SUPPORT_TOPICS:
        topic = "General"

    if not request_id:
        request_id = f"REQ-{int(datetime.now(timezone.utc).timestamp())}"

    return {
        "id": request_id,
        "subject": subject,
        "message": message,
        "submitted_by": submitted_by,
        "workspace_name": workspace_name,
        "topic": topic,
        "created_at": created_at,
    }, None


def _build_support_email(config: dict, request: dict) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config["from_email"]
    message["To"] = config["to_email"]
    message["Subject"] = f"[Cuevion support] {request['subject']}"

    body = "\n".join(
        [
            "Cuevion support request",
            "",
            f"Request ID: {request['id']}",
            f"Created at: {request['created_at']}",
            f"Submitted by: {request['submitted_by']}",
            f"Workspace: {request['workspace_name']}",
            f"Topic: {request['topic']}",
            "",
            "Subject:",
            request["subject"],
            "",
            "Message:",
            request["message"],
        ],
    )
    message.set_content(body)
    return message


def _send_support_email(config: dict, message: EmailMessage):
    context = ssl.create_default_context()

    if config["secure"] == "ssl":
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30, context=context) as smtp:
            smtp.login(config["username"], config["password"])
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
        smtp.starttls(context=context)
        smtp.login(config["username"], config["password"])
        smtp.send_message(message)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        session_secret = resolve_beta_session_secret()
        session_user = _resolve_authenticated_user(self.headers)
        if session_secret and not session_user:
            _send_json(self, 401, _build_error("unauthorized", "A valid beta session is required."))
            return

        payload, payload_error = _read_json_body(self)
        if payload_error:
            _send_json(self, 400, payload_error)
            return

        config, config_error = _resolve_smtp_config()
        if config_error:
            _send_json(self, 503, config_error)
            return

        support_request, request_error = _normalize_support_payload(payload or {})
        if request_error:
            _send_json(self, 400, request_error)
            return

        email_message = _build_support_email(config, support_request)

        try:
            _send_support_email(config, email_message)
        except smtplib.SMTPAuthenticationError:
            _send_json(self, 502, _build_error("send_failed", "Support request could not be sent."))
            return
        except smtplib.SMTPException:
            _send_json(self, 502, _build_error("send_failed", "Support request could not be sent."))
            return
        except Exception:
            _send_json(self, 500, _build_error("server_error", "Support request could not be sent."))
            return

        _send_json(self, 200, {"ok": True})

    def do_GET(self):
        _send_json(self, 405, _build_error("method_not_allowed", "Use POST for support requests."))

    def log_message(self, format, *args):
        return
