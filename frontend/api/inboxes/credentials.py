import json
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
API_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from mailbox_secret_store import (  # noqa: E402
    is_valid_mailbox_credential_version,
    read_mailbox_secret,
)
from user_config_store import (  # noqa: E402
    resolve_authenticated_user,
    resolve_owned_managed_inbox_record,
)

# Keep the route-local patch seam used by existing auth tests while resolving
# the full server-side record (including credentialVersion), not the public
# generation-free projection.
resolve_owned_managed_inbox = resolve_owned_managed_inbox_record


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
    content_length = int(handler.headers.get("content-length", "0"))
    raw_body = handler.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""

    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None, _build_error("invalid_request", "Request body must be valid JSON.")

    if not isinstance(payload, dict):
        return None, _build_error("invalid_request", "Request body must be a JSON object.")

    return payload, None


def _parse_mailbox_ids_from_query(path: str) -> list[str]:
    query = parse_qs(urlsplit(path).query)
    raw_mailbox_ids = query.get("mailboxIds") or []
    mailbox_ids: list[str] = []

    for raw_value in raw_mailbox_ids:
        mailbox_ids.extend(
            mailbox_id.strip()
            for mailbox_id in raw_value.split(",")
            if mailbox_id.strip()
        )

    return mailbox_ids


def _valid_port(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        port = int(str(value))
    except (TypeError, ValueError):
        return False
    return 1 <= port <= 65535


def _stored_password_is_usable(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip().casefold()
    return (
        normalized
        not in {
            "stored securely",
            "stored securely — leave blank to reuse",
        }
        and re.fullmatch(r"[*•●]{6,}", normalized) is None
    )


def _smtp_credential_source(inbox: dict) -> str | None:
    custom_smtp = inbox.get("customSmtp")
    if not isinstance(custom_smtp, dict) or not custom_smtp:
        return None
    if set(custom_smtp) != {
        "host",
        "port",
        "security",
        "username",
        "useSameCredentials",
    }:
        return None
    host = custom_smtp.get("host")
    username = custom_smtp.get("username")
    use_same_credentials = custom_smtp.get("useSameCredentials")
    if (
        not isinstance(host, str)
        or not host
        or host != host.strip()
        or not _valid_port(custom_smtp.get("port"))
        or custom_smtp.get("security") not in {"ssl", "starttls"}
        or not isinstance(username, str)
        or not username
        or username != username.strip()
        or not isinstance(use_same_credentials, bool)
    ):
        return None
    return "imap" if use_same_credentials else "smtp"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        session_user, auth_error = resolve_authenticated_user(self.headers)
        if not session_user:
            if auth_error and auth_error.get("code") == "session_auth_unavailable":
                _send_json(
                    self,
                    503,
                    _build_error(
                        "session_auth_unavailable",
                        "Authentication is temporarily unavailable.",
                    ),
                )
            else:
                _send_json(
                    self,
                    401,
                    _build_error(
                        "unauthorized",
                        "A valid member session is required.",
                    ),
                )
            return

        mailbox_ids = _parse_mailbox_ids_from_query(self.path)
        credentials: dict[str, dict] = {}
        for mailbox_id in mailbox_ids:
            owned_result = resolve_owned_managed_inbox(self.headers, mailbox_id)
            if owned_result["status"] != "ok":
                status_code = 503 if owned_result["status"] == "unavailable" else 404
                _send_json(
                    self,
                    status_code,
                    _build_error(
                        "mailbox_status_unavailable"
                        if status_code == 503
                        else "managed_inbox_not_found",
                        "Mailbox credential status is unavailable."
                        if status_code == 503
                        else "The requested mailbox was not found.",
                    ),
                )
                return

            inbox = owned_result.get("inbox")
            config_generation = (
                inbox.get("credentialVersion") if isinstance(inbox, dict) else None
            )
            config_can_reference_credentials = (
                isinstance(inbox, dict)
                and inbox.get("provider") == "custom_imap"
                and inbox.get("connected") is True
                and inbox.get("connectionStatus") == "connected"
                and is_valid_mailbox_credential_version(config_generation)
            )
            # Preserve the route's established canonical-owner lookup seam for a
            # minimal legacy status projection. It can never report credentials
            # as present because it has no valid config generation.
            is_minimal_legacy_projection = (
                isinstance(inbox, dict)
                and set(inbox).issubset({"id"})
                and isinstance(inbox.get("id"), str)
            )
            if (
                not config_can_reference_credentials
                and not is_minimal_legacy_projection
            ):
                credentials[mailbox_id] = {
                    "imapPasswordSet": False,
                    "smtpPasswordSet": False,
                }
                continue

            try:
                secret_result = read_mailbox_secret(
                    session_user["email"],
                    mailbox_id,
                )
            except Exception:
                secret_result = None
            if not isinstance(secret_result, dict):
                _send_json(
                    self,
                    503,
                    _build_error(
                        "mailbox_secret_store_unavailable",
                        "Mailbox credential status is temporarily unavailable.",
                    ),
                )
                return

            secret_status = secret_result.get("status")
            secret_record = secret_result.get("record")
            secret_error = secret_result.get("error")
            if secret_status == "missing":
                if (
                    "record" not in secret_result
                    or secret_record is not None
                    or secret_error is not None
                ):
                    _send_json(
                        self,
                        503,
                        _build_error(
                            "mailbox_secret_store_unavailable",
                            "Mailbox credential status is temporarily unavailable.",
                        ),
                    )
                    return
                credentials[mailbox_id] = {
                    "imapPasswordSet": False,
                    "smtpPasswordSet": False,
                }
                continue
            if (
                secret_status != "present"
                or not isinstance(secret_record, dict)
                or secret_error is not None
            ):
                _send_json(
                    self,
                    503,
                    _build_error(
                        "mailbox_secret_store_unavailable",
                        "Mailbox credential status is temporarily unavailable.",
                    ),
                )
                return

            secret_generation = secret_record.get("credentialVersion")
            generation_matches = (
                is_valid_mailbox_credential_version(secret_generation)
                and config_generation == secret_generation
            )
            imap_password = secret_record.get("imapPassword")
            smtp_password = secret_record.get("smtpPassword")
            smtp_credential_source = (
                _smtp_credential_source(inbox)
                if isinstance(inbox, dict)
                else None
            )
            credentials[mailbox_id] = {
                "imapPasswordSet": (
                    generation_matches
                    and _stored_password_is_usable(imap_password)
                ),
                "smtpPasswordSet": (
                    generation_matches
                    and smtp_credential_source is not None
                    and _stored_password_is_usable(
                        imap_password
                        if smtp_credential_source == "imap"
                        else smtp_password
                    )
                ),
            }

        _send_json(
            self,
            200,
            {
                "ok": True,
                "credentials": credentials,
            },
        )

    def do_POST(self):
        _send_json(
            self,
            405,
            _build_error(
                "method_not_allowed",
                "Mailbox credentials can only be saved during authenticated connection.",
            ),
        )

    def do_OPTIONS(self):
        _send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
