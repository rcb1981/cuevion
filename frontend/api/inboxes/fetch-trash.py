import sys as _identity_sys

_CANONICAL_MODULE_NAME = "api.inboxes.fetch-trash"
_LEGACY_MODULE_NAME = "fetch-trash"
_CANONICAL_PACKAGE_NAME = "api.inboxes"

_loaded_module_name = __name__
_current_module = _identity_sys.modules[_loaded_module_name]
if _loaded_module_name not in {_CANONICAL_MODULE_NAME, _LEGACY_MODULE_NAME}:
    raise ImportError(
        "Gmail Trash route helpers must be imported as "
        + _CANONICAL_MODULE_NAME
    )

_existing_canonical = _identity_sys.modules.get(_CANONICAL_MODULE_NAME)
_existing_legacy = _identity_sys.modules.get(_LEGACY_MODULE_NAME)
if (
    (
        _existing_canonical is not None
        and _existing_canonical is not _current_module
    )
    or (
        _existing_legacy is not None
        and _existing_legacy is not _current_module
    )
):
    raise ImportError(
        "canonical and legacy Gmail Trash route identities cannot coexist"
    )

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
    valid_identifier,
)
from .gmail_snapshot import (
    GMAIL_API_UID_VALIDITY,
    read_gmail_folder_snapshot,
)

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
TRASH_FETCH_LIMIT = 100

_FORBIDDEN_PUBLIC_KEYS = {
    "accesstoken",
    "authorization",
    "connection",
    "cookie",
    "credentialgeneration",
    "credentialversion",
    "fingerprint",
    "identities",
    "identity",
    "imapuid",
    "mailboxconfig",
    "owneremail",
    "password",
    "providerdetails",
    "providererror",
    "raw",
    "rawproviderresponse",
    "refreshtoken",
    "secretgeneration",
    "secretversion",
    "session",
    "tokenrecord",
    "userid",
    "username",
}
_FORBIDDEN_PUBLIC_KEY_FRAGMENTS = {
    "credential",
    "fingerprint",
    "password",
    "secret",
    "token",
}


def _contains_forbidden_public_fields(value: object) -> bool:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                return True
            compact_key = "".join(
                character
                for character in key.casefold()
                if character.isalnum()
            )
            if (
                compact_key in _FORBIDDEN_PUBLIC_KEYS
                or any(
                    fragment in compact_key
                    for fragment in _FORBIDDEN_PUBLIC_KEY_FRAGMENTS
                )
                or _contains_forbidden_public_fields(item)
            ):
                return True
        return False
    if type(value) is list:
        return any(_contains_forbidden_public_fields(item) for item in value)
    return False


def _gmail_request(
    access_token: str,
    path: str,
) -> tuple[dict | None, dict | None]:
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
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
            if body is None:
                return None, {"code": "gmail_response_too_large"}
            if not body:
                return None, {"code": "gmail_response_invalid"}
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None, {"code": "gmail_response_invalid"}
            if not isinstance(payload, dict):
                return None, {"code": "gmail_response_invalid"}
            return payload, None
    except HTTPError as error:
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_fetch_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _request_with_one_refresh(context: dict, path: str):
    payload, request_error = _gmail_request(context["access_token"], path)
    if (
        request_error
        and request_error.get("code") == "gmail_token_invalid"
        and not context["refresh_attempted"]
    ):
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, request_error, context, refreshed
        context = refreshed["context"]
        payload, request_error = _gmail_request(
            context["access_token"],
            path,
        )
    return payload, request_error, context, None


def _send_gmail_error(target, request_error: dict):
    code = request_error.get("code")
    mapping = {
        "gmail_token_invalid": (
            401,
            "reconnect_required",
            "Reconnect this Gmail inbox to continue.",
        ),
        "gmail_permission_denied": (
            403,
            "gmail_permission_denied",
            "Gmail did not permit this operation.",
        ),
        "gmail_rate_limited": (
            502,
            "gmail_rate_limited",
            "Gmail is temporarily rate limited.",
        ),
        "gmail_unavailable": (
            502,
            "gmail_unavailable",
            "Gmail is temporarily unavailable.",
        ),
        "gmail_response_invalid": (
            502,
            "gmail_response_invalid",
            "Gmail returned an invalid response.",
        ),
        "gmail_response_too_large": (
            502,
            "gmail_response_too_large",
            "Gmail returned a response that is too large.",
        ),
    }
    status, safe_code, message = mapping.get(
        code,
        (502, "gmail_fetch_failed", "Gmail Trash could not be loaded."),
    )
    send_json(target, status, error_payload(safe_code, message))


def _trash_snapshot_is_valid(snapshot: object, mailbox_id: str) -> bool:
    if (
        type(snapshot) is not dict
        or set(snapshot)
        != {
            "serverMailboxId",
            "providerFolder",
            "messages",
            "uidValidity",
        }
        or snapshot.get("serverMailboxId") != mailbox_id
        or snapshot.get("providerFolder") != "Trash"
        or snapshot.get("uidValidity") != GMAIL_API_UID_VALIDITY
        or not isinstance(snapshot.get("messages"), list)
        or len(snapshot["messages"]) > TRASH_FETCH_LIMIT
        or _contains_forbidden_public_fields(snapshot)
    ):
        return False

    seen_message_ids: set[str] = set()
    for message in snapshot["messages"]:
        if (
            type(message) is not dict
            or message.get("serverMailboxId") != mailbox_id
            or message.get("providerFolder") != "Trash"
            or not valid_identifier(message.get("providerMessageId"))
            or not valid_identifier(message.get("providerThreadId"))
            or "imapUid" in message
        ):
            return False

        provider_message_id = message["providerMessageId"]
        if provider_message_id in seen_message_ids:
            return False
        seen_message_ids.add(provider_message_id)

        label_ids = message.get("labelIds")
        if (
            not isinstance(label_ids, list)
            or not all(valid_identifier(label_id) for label_id in label_ids)
            or len(set(label_ids)) != len(label_ids)
        ):
            return False
        normalized_labels = {label_id.upper() for label_id in label_ids}
        if "TRASH" not in normalized_labels or "INBOX" in normalized_labels:
            return False

        rfc_message_id = message.get("rfcMessageId")
        if rfc_message_id is not None and not valid_identifier(rfc_message_id):
            return False
    return True


def _trash_success_payload(
    *,
    mailbox_id: str,
    snapshot: object,
) -> dict | None:
    if not _trash_snapshot_is_valid(snapshot, mailbox_id):
        return None
    payload = {
        "ok": True,
        "status": "ok",
        "mailboxId": mailbox_id,
        "folder": snapshot,
    }
    try:
        if (
            len(json.dumps(payload).encode("utf-8"))
            > MAX_GMAIL_RESPONSE_BYTES
        ):
            return None
    except (TypeError, UnicodeEncodeError, ValueError):
        return None
    return payload


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for Gmail Trash fetch.",
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
                error_payload(
                    "internal_error",
                    "The Gmail Trash request could not be completed.",
                ),
            )

    def _handle_post(self):
        payload, request_error = read_json_body(self)
        if request_error:
            send_json(
                self,
                (
                    413
                    if request_error["error"]["code"] == "request_too_large"
                    else 400
                ),
                request_error,
            )
            return

        field_error = reject_unknown_fields(payload, {"mailboxId"})
        if (
            field_error
            or set(payload) != {"mailboxId"}
            or not valid_identifier(payload.get("mailboxId"))
        ):
            send_json(
                self,
                400,
                error_payload(
                    "invalid_request",
                    "Trash fetch requires one managed mailbox.",
                ),
            )
            return

        resolution = resolve_authenticated_gmail(
            self.headers,
            payload["mailboxId"],
        )
        if resolution["status"] != "ok":
            send_json(
                self,
                resolution["status_code"],
                resolution["error"],
            )
            return
        context = resolution["context"]

        snapshot_result = read_gmail_folder_snapshot(
            context,
            provider_folder="Trash",
            request_with_one_refresh=_request_with_one_refresh,
            limit=TRASH_FETCH_LIMIT,
            focus_preferences=None,
            strict=True,
            message_parser=message_from_bytes,
        )
        refresh_failure = snapshot_result.get("refresh_failure")
        if refresh_failure:
            send_json(
                self,
                refresh_failure["status_code"],
                refresh_failure["error"],
            )
            return
        snapshot_error = snapshot_result.get("error")
        if snapshot_error:
            _send_gmail_error(self, snapshot_error)
            return

        success_payload = _trash_success_payload(
            mailbox_id=context["mailbox_id"],
            snapshot=snapshot_result.get("snapshot"),
        )
        if success_payload is None:
            send_json(
                self,
                502,
                error_payload(
                    "trash_snapshot_failed",
                    "Gmail Trash could not be verified safely.",
                ),
            )
            return
        send_json(self, 200, success_payload)

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Gmail Trash fetch.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST for Gmail Trash fetch.",
            write_body=False,
        )

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
