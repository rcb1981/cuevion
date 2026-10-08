from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

CURRENT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = CURRENT_DIR.parent
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from api.out_of_office_store import (  # noqa: E402
    OutOfOfficeStoreUnavailable,
    OutOfOfficeValidationError,
    build_out_of_office_store,
    default_out_of_office_settings,
    normalize_out_of_office_settings,
)
from api.user_config_store import resolve_owned_managed_inbox_record  # noqa: E402
from api.out_of_office_providers import resolve_out_of_office_adapter  # noqa: E402
from api.out_of_office_worker import OutOfOfficeProviderError  # noqa: E402

MAX_REQUEST_BODY_BYTES = 32 * 1024


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _mailbox_id_from_query(path: str) -> str:
    values = parse_qs(urlsplit(path).query, keep_blank_values=True).get("mailboxId", [])
    if len(values) != 1:
        return ""
    value = values[0].strip()
    return value if value and len(value) <= 256 else ""


def _read_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict | None, dict | None]:
    raw_length = handler.headers.get("content-length", "")
    if not raw_length.isascii() or not raw_length.isdigit():
        return None, _error("invalid_request", "Content-Length is required.")
    length = int(raw_length)
    if length <= 0 or length > MAX_REQUEST_BODY_BYTES:
        return None, _error("invalid_request", "Request body size is invalid.")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        return None, _error("invalid_request", "Request body is incomplete.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error("invalid_request", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return None, _error("invalid_request", "Request body must be a JSON object.")
    return payload, None


def _resolve_owner(handler: BaseHTTPRequestHandler, mailbox_id: str):
    result = resolve_owned_managed_inbox_record(handler.headers, mailbox_id)
    status = result.get("status") if isinstance(result, dict) else None
    if status == "ok":
        user = result.get("user")
        inbox = result.get("inbox")
        if isinstance(user, dict) and isinstance(user.get("email"), str) and isinstance(inbox, dict):
            return user, inbox, None

    if status == "unauthorized":
        return None, None, (401, _error("unauthorized", "A valid owner session is required."))
    if status == "not_found":
        return None, None, (404, _error("mailbox_not_found", "Mailbox was not found."))
    if status in {"unavailable", "malformed"}:
        return None, None, (
            503,
            _error("out_of_office_unavailable", "Out of office settings are temporarily unavailable."),
        )
    return None, None, (403, _error("forbidden", "Mailbox access could not be verified."))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        mailbox_id = _mailbox_id_from_query(self.path)
        if not mailbox_id:
            _send_json(self, 400, _error("invalid_mailbox_id", "mailboxId is required."))
            return

        user, _inbox, failure = _resolve_owner(self, mailbox_id)
        if failure:
            _send_json(self, failure[0], failure[1])
            return

        try:
            store = build_out_of_office_store()
            settings = store.get(user["email"], mailbox_id)
        except OutOfOfficeStoreUnavailable:
            _send_json(
                self,
                503,
                _error("out_of_office_unavailable", "Out of office settings are temporarily unavailable."),
            )
            return

        if settings is None:
            settings = default_out_of_office_settings()

        _send_json(self, 200, {"ok": True, "mailboxId": mailbox_id, "settings": settings})

    def do_POST(self):
        payload, parse_error = _read_json_body(self)
        if parse_error:
            _send_json(self, 400, parse_error)
            return
        if set(payload) != {"mailboxId", "settings"}:
            _send_json(self, 400, _error("invalid_request", "Request fields are invalid."))
            return

        mailbox_id = payload.get("mailboxId")
        if not isinstance(mailbox_id, str) or not mailbox_id.strip() or len(mailbox_id.strip()) > 256:
            _send_json(self, 400, _error("invalid_mailbox_id", "mailboxId is invalid."))
            return
        mailbox_id = mailbox_id.strip()

        user, _inbox, failure = _resolve_owner(self, mailbox_id)
        if failure:
            _send_json(self, failure[0], failure[1])
            return

        raw_settings = payload.get("settings")
        allowed_settings_fields = {"enabled", "startsAt", "endsAt", "subject", "message"}
        if not isinstance(raw_settings, dict) or set(raw_settings) - allowed_settings_fields:
            _send_json(self, 400, _error("invalid_settings", "settings fields are invalid."))
            return

        try:
            settings = normalize_out_of_office_settings(raw_settings)
        except OutOfOfficeValidationError as exc:
            _send_json(self, 400, _error("invalid_settings", str(exc)))
            return

        try:
            store = build_out_of_office_store()
            existing = store.get(user["email"], mailbox_id)
            cursor = store.get_cursor(user["email"], mailbox_id)
        except OutOfOfficeStoreUnavailable:
            _send_json(
                self,
                503,
                _error("out_of_office_unavailable", "Out of office settings could not be saved."),
            )
            return

        if settings["enabled"]:
            provider = _inbox.get("provider") if isinstance(_inbox, dict) else None
            activation_changed = (
                existing is None
                or existing.get("enabled") is not True
                or existing.get("startsAt") != settings.get("startsAt")
                or existing.get("endsAt") != settings.get("endsAt")
                or not isinstance(existing.get("activatedAt"), str)
                or cursor is None
                or cursor.get("provider") != provider
            )
            if activation_changed:
                settings["activatedAt"] = settings["updatedAt"]
                try:
                    adapter = resolve_out_of_office_adapter(_inbox)
                    baseline_now = datetime.fromisoformat(
                        settings["updatedAt"].replace("Z", "+00:00")
                    )
                    baseline = adapter.baseline(
                        _inbox,
                        owner_email=user["email"],
                        now=baseline_now,
                    )
                    store.put_with_cursor(
                        user["email"],
                        mailbox_id,
                        settings,
                        baseline,
                    )
                except OutOfOfficeProviderError as exc:
                    status_code = (
                        409
                        if exc.code.endswith("reconnect_required")
                        else 503
                    )
                    _send_json(
                        self,
                        status_code,
                        _error(exc.code, "Out of office could not be activated for this mailbox."),
                    )
                    return
                except (OutOfOfficeStoreUnavailable, ValueError):
                    _send_json(
                        self,
                        503,
                        _error("out_of_office_unavailable", "Out of office settings could not be saved."),
                    )
                    return
            else:
                settings["activatedAt"] = existing["activatedAt"]
                try:
                    store.put(user["email"], mailbox_id, settings)
                except OutOfOfficeStoreUnavailable:
                    _send_json(
                        self,
                        503,
                        _error("out_of_office_unavailable", "Out of office settings could not be saved."),
                    )
                    return
        else:
            settings["activatedAt"] = None
            try:
                store.put(user["email"], mailbox_id, settings)
            except OutOfOfficeStoreUnavailable:
                _send_json(
                    self,
                    503,
                    _error("out_of_office_unavailable", "Out of office settings could not be saved."),
                )
                return

        _send_json(self, 200, {"ok": True, "mailboxId": mailbox_id, "settings": settings})

    def do_PUT(self):
        _send_json(self, 405, _error("method_not_allowed", "Use GET or POST."))

    def do_DELETE(self):
        _send_json(self, 405, _error("method_not_allowed", "Use GET or POST."))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format, *args):
        return
