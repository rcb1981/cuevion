import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.fetch-gmail"
_LEGACY_MODULE_NAME = "fetch-gmail"
_CANONICAL_PACKAGE_NAME = "api.inboxes"

_loaded_module_name = __name__
_current_module = _identity_sys.modules[_loaded_module_name]
if _loaded_module_name not in {_CANONICAL_MODULE_NAME, _LEGACY_MODULE_NAME}:
    raise ImportError(
        "Gmail route helpers must be imported as " + _CANONICAL_MODULE_NAME
    )

_existing_canonical = _identity_sys.modules.get(_CANONICAL_MODULE_NAME)
_existing_legacy = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
if (
    (_existing_canonical is not None and _existing_canonical is not _current_module)
    or (_existing_legacy is not None and _existing_legacy is not _current_module)
):
    raise ImportError("canonical and legacy Gmail route identities cannot coexist")

if _loaded_module_name == _LEGACY_MODULE_NAME:
    _current_module.__name__ = _CANONICAL_MODULE_NAME
    _current_module.__package__ = _CANONICAL_PACKAGE_NAME

_identity_sys.modules[_CANONICAL_MODULE_NAME] = _current_module
_identity_sys.modules[_LEGACY_MODULE_NAME] = _current_module

import json
from email import message_from_bytes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES,
    error_payload,
    gmail_http_error_code,
    read_bounded_response,
    read_json_body,
    refresh_gmail_context,
    reject_unknown_fields,
    resolve_authenticated_gmail,
    send_json,
    send_method_not_allowed,
    validate_focus_preferences,
    valid_identifier,
)
from .gmail_snapshot import read_gmail_folder_snapshot
from api.priority.candidate_projection import (
    populate_runtime_priority_candidates,
)
from api.priority.semantic_config import read_new_inbound_client_mode

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_FETCH_LIMIT = 50
MAX_FETCH_LIMIT = 100


def _validate_focus_preferences(value: object) -> tuple[dict | None, dict | None]:
    return validate_focus_preferences(value)


def _has_explicit_inbox_membership(message: object) -> bool:
    if not isinstance(message, dict):
        return False

    label_ids = message.get("labelIds")
    return (
        isinstance(label_ids, list)
        and all(valid_identifier(label_id) for label_id in label_ids)
        and len(set(label_ids)) == len(label_ids)
        and "INBOX" in label_ids
    )


def _gmail_request(access_token: str, path: str) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
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
        return None, {"code": gmail_http_error_code(error.code, "gmail_fetch_failed")}
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _send_gmail_error(handler, error: dict):
    code = error.get("code")
    mapping = {
        "gmail_token_invalid": (401, "reconnect_required", "Reconnect this Gmail inbox to continue."),
        "gmail_permission_denied": (403, "gmail_permission_denied", "Gmail did not permit this operation."),
        "gmail_rate_limited": (502, "gmail_rate_limited", "Gmail is temporarily rate limited."),
        "gmail_unavailable": (502, "gmail_unavailable", "Gmail is temporarily unavailable."),
        "gmail_response_invalid": (502, "gmail_response_invalid", "Gmail returned an invalid response."),
        "gmail_response_too_large": (502, "gmail_response_too_large", "Gmail returned a response that is too large."),
    }
    status, safe_code, message = mapping.get(
        code,
        (502, "gmail_fetch_failed", "Gmail inbox could not be loaded."),
    )
    send_json(handler, status, error_payload(safe_code, message))


def _request_with_one_refresh(context: dict, path: str):
    payload, error = _gmail_request(context["access_token"], path)
    if error and error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, error, context, refreshed
        context = refreshed["context"]
        payload, error = _gmail_request(context["access_token"], path)
    return payload, error, context, None


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for Gmail mailbox fetch.",
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
                error_payload("internal_error", "The Gmail request could not be completed."),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(self, 400 if request_error["error"]["code"] != "request_too_large" else 413, request_error)
            return
        field_error = reject_unknown_fields(payload, {"mailboxId", "focusPreferences", "limit"})
        if field_error:
            send_json(self, 400, field_error)
            return

        limit_value = payload.get("limit", DEFAULT_FETCH_LIMIT)
        if limit_value is None:
            limit_value = DEFAULT_FETCH_LIMIT
        if not isinstance(limit_value, int) or isinstance(limit_value, bool):
            send_json(self, 400, error_payload("invalid_request", "Fetch limit must be an integer."))
            return
        limit = max(1, min(limit_value, MAX_FETCH_LIMIT))

        focus_preferences = None
        if "focusPreferences" in payload:
            focus_preferences, focus_error = _validate_focus_preferences(
                payload.get("focusPreferences")
            )
            if focus_error:
                send_json(self, 400, focus_error)
                return

        resolution = resolve_authenticated_gmail(
            self.headers,
            payload.get("mailboxId"),
            include_member_authority=True,
        )
        if resolution["status"] != "ok":
            send_json(self, resolution["status_code"], resolution["error"])
            return
        context = resolution["context"]

        snapshot_result = read_gmail_folder_snapshot(
            context,
            provider_folder="Inbox",
            request_with_one_refresh=_request_with_one_refresh,
            limit=limit,
            focus_preferences=focus_preferences,
            strict=False,
            message_parser=message_from_bytes,
        )
        refresh_failure = snapshot_result.get("refresh_failure")
        if refresh_failure:
            send_json(self, refresh_failure["status_code"], refresh_failure["error"])
            return
        snapshot_error = snapshot_result.get("error")
        if snapshot_error:
            _send_gmail_error(self, snapshot_error)
            return
        snapshot = snapshot_result.get("snapshot")
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("messages"),
            list,
        ):
            _send_gmail_error(self, {"code": "gmail_response_invalid"})
            return

        previews = [
            message
            for message in snapshot["messages"]
            if _has_explicit_inbox_membership(message)
        ]
        inbox_uid_set = [
            message["providerMessageId"]
            for message in previews
            if isinstance(message, dict)
            and valid_identifier(message.get("providerMessageId"))
        ]

        candidate_sources = snapshot_result.get("_priorityCandidateSources")
        if isinstance(candidate_sources, list) and candidate_sources:
            try:
                populate_runtime_priority_candidates(
                    member=resolution.get("memberAuthority"),
                    mailbox_id=context.get("mailbox_id"),
                    mailbox_account_identity=context.get("mailbox_email"),
                    provider="google",
                    sources=candidate_sources,
                )
            except Exception:
                pass

        send_json(
            self,
            200,
            {
                "ok": True,
                "messages": previews,
                "inboxUidSet": inbox_uid_set,
                "uidValidity": snapshot.get("uidValidity", "gmail-api"),
                "prioritySemanticNewInboundMode": (
                    read_new_inbound_client_mode()
                ),
            },
        )

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Gmail mailbox fetch.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST for Gmail mailbox fetch.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
