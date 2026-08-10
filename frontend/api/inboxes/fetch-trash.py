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

import imaplib
import json
import re
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
    resolve_gmail_context,
    resolve_owned_mailbox,
    send_json,
    send_method_not_allowed,
    valid_identifier,
)
from .gmail_snapshot import (
    GMAIL_API_UID_VALIDITY,
    read_gmail_folder_snapshot,
)
from . import imap_trash
from .authenticated_imap import resolve_authenticated_imap_mailbox
from .imap_snapshot import read_imap_folder_snapshot
from .imap_uid_validity import is_canonical_uid_validity
from imap_connect_preview import connect_mailbox_with_settings

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
TRASH_FETCH_LIMIT = 100
MAX_IMAP_UID_SET_SIZE = 100_000
_MAX_IMAP_UID = 4_294_967_295
_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)

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
_IMAP_FORBIDDEN_PUBLIC_KEYS = (
    _FORBIDDEN_PUBLIC_KEYS
    - {"imapuid"}
    | {
        "authmode",
        "customimap",
        "host",
        "imaphost",
        "imapport",
        "imapusername",
        "labelids",
        "port",
        "provider",
        "providermessageid",
        "providerthreadid",
        "ssl",
    }
)
_IMAP_FORBIDDEN_PUBLIC_KEY_FRAGMENTS = (
    _FORBIDDEN_PUBLIC_KEY_FRAGMENTS | {"hash"}
)


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


def _contains_forbidden_imap_public_fields(value: object) -> bool:
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
                compact_key in _IMAP_FORBIDDEN_PUBLIC_KEYS
                or any(
                    fragment in compact_key
                    for fragment in _IMAP_FORBIDDEN_PUBLIC_KEY_FRAGMENTS
                )
                or _contains_forbidden_imap_public_fields(item)
            ):
                return True
        return False
    if type(value) is list:
        return any(
            _contains_forbidden_imap_public_fields(item)
            for item in value
        )
    return False


def _valid_imap_uid(value: object) -> bool:
    if type(value) is not str or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _valid_public_text(value: object, *, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        )
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_private_text(value: object, *, maximum_bytes: int) -> bool:
    if (
        type(value) is not str
        or not value
        or any(
            ord(character) < 32 or 127 <= ord(character) <= 159
            for character in value
        )
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeEncodeError:
        return False


def _valid_resolved_imap_mailbox(
    value: object,
    *,
    mailbox_id: str,
) -> bool:
    if type(value) is not dict or value.get("mailboxId") != mailbox_id:
        return False

    email = value.get("email")
    if (
        not _valid_public_text(email, maximum_bytes=4_096)
        or re.fullmatch(r"[^@\s]+@[^@\s]+", email) is None
    ):
        return False

    imap = value.get("imap")
    if type(imap) is not dict or set(imap) != {
        "host",
        "port",
        "ssl",
        "username",
        "password",
    }:
        return False
    host = imap.get("host")
    port = imap.get("port")
    return (
        _valid_public_text(host, maximum_bytes=4_096)
        and not any(character.isspace() for character in host)
        and type(port) is int
        and 1 <= port <= 65_535
        and imap.get("ssl") is True
        and _valid_public_text(imap.get("username"), maximum_bytes=4_096)
        and _valid_private_text(imap.get("password"), maximum_bytes=65_536)
    )


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


def _imap_trash_snapshot_is_valid(
    snapshot: object,
    *,
    mailbox_id: str,
    trash_folder: str,
) -> bool:
    if (
        type(snapshot) is not dict
        or set(snapshot)
        != {
            "serverMailboxId",
            "providerFolder",
            "uidValidity",
            "imapUidSet",
            "messages",
        }
        or snapshot.get("serverMailboxId") != mailbox_id
        or snapshot.get("providerFolder") != trash_folder
        or not is_canonical_uid_validity(snapshot.get("uidValidity"))
        or type(snapshot.get("imapUidSet")) is not list
        or len(snapshot["imapUidSet"]) > MAX_IMAP_UID_SET_SIZE
        or type(snapshot.get("messages")) is not list
        or len(snapshot["messages"]) > TRASH_FETCH_LIMIT
        or _contains_forbidden_imap_public_fields(snapshot)
    ):
        return False

    imap_uid_set = snapshot["imapUidSet"]
    if (
        any(not _valid_imap_uid(uid) for uid in imap_uid_set)
        or len(set(imap_uid_set)) != len(imap_uid_set)
        or imap_uid_set
        != sorted(imap_uid_set, key=lambda candidate: int(candidate))
    ):
        return False

    known_uids = set(imap_uid_set)
    uid_validity = snapshot["uidValidity"]
    message_uids: list[str] = []
    seen_message_uids: set[str] = set()
    for message in snapshot["messages"]:
        if (
            type(message) is not dict
            or message.get("serverMailboxId") != mailbox_id
            or message.get("providerFolder") != trash_folder
            or message.get("uidValidity") != uid_validity
            or not _valid_imap_uid(message.get("imapUid"))
            or message["imapUid"] not in known_uids
            or message["imapUid"] in seen_message_uids
        ):
            return False
        if (
            "rfcMessageId" in message
            and not _valid_public_text(
                message["rfcMessageId"],
                maximum_bytes=4_096,
            )
        ):
            return False
        message_uids.append(message["imapUid"])
        seen_message_uids.add(message["imapUid"])
    return message_uids == list(
        reversed(imap_uid_set[-TRASH_FETCH_LIMIT:])
    )


def _imap_trash_success_payload(
    *,
    mailbox_id: str,
    trash_folder: str,
    snapshot: object,
) -> dict | None:
    if not _imap_trash_snapshot_is_valid(
        snapshot,
        mailbox_id=mailbox_id,
        trash_folder=trash_folder,
    ):
        return None
    payload = {
        "ok": True,
        "status": "ok",
        "provider": "custom_imap",
        "mailboxId": mailbox_id,
        "folder": snapshot,
    }
    try:
        if len(json.dumps(payload).encode("utf-8")) > MAX_GMAIL_RESPONSE_BYTES:
            return None
    except (TypeError, UnicodeEncodeError, ValueError):
        return None
    return payload


def _send_imap_resolution_error(target, resolution: dict):
    error = resolution.get("error") if isinstance(resolution, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    mapping = {
        "unauthorized": (
            401,
            "A valid member session is required.",
        ),
        "managed_inbox_not_found": (
            404,
            "The requested mailbox was not found.",
        ),
        "reconnect_required": (
            409,
            "Reconnect this mailbox to continue.",
        ),
        "mailbox_configuration_unavailable": (
            503,
            "Mailbox configuration is temporarily unavailable.",
        ),
        "mailbox_secret_store_unavailable": (
            503,
            "Mailbox credentials are temporarily unavailable.",
        ),
        "mailbox_configuration_malformed": (
            500,
            "Mailbox configuration is invalid.",
        ),
        "mailbox_secret_malformed": (
            500,
            "Stored mailbox credentials are invalid.",
        ),
    }
    status_code, message = mapping.get(
        code,
        (500, "Mailbox configuration is invalid."),
    )
    safe_code = code if code in mapping else "mailbox_configuration_malformed"
    send_json(target, status_code, error_payload(safe_code, message))


def _send_imap_trash_snapshot_failed(target):
    send_json(
        target,
        502,
        error_payload(
            "trash_snapshot_failed",
            "The Trash mailbox could not be loaded safely.",
        ),
    )


def _perform_gmail_trash_snapshot(
    target,
    *,
    owned: dict,
):
    resolution = resolve_gmail_context(owned)
    if resolution["status"] != "ok":
        send_json(
            target,
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
            target,
            refresh_failure["status_code"],
            refresh_failure["error"],
        )
        return
    snapshot_error = snapshot_result.get("error")
    if snapshot_error:
        _send_gmail_error(target, snapshot_error)
        return

    success_payload = _trash_success_payload(
        mailbox_id=context["mailbox_id"],
        snapshot=snapshot_result.get("snapshot"),
    )
    if success_payload is None:
        send_json(
            target,
            502,
            error_payload(
                "trash_snapshot_failed",
                "Gmail Trash could not be verified safely.",
            ),
        )
        return
    send_json(target, 200, success_payload)


def _perform_imap_trash_snapshot(
    target,
    *,
    mailbox_id: str,
):
    resolution = resolve_authenticated_imap_mailbox(
        target.headers,
        mailbox_id,
    )
    if (
        not isinstance(resolution, dict)
        or resolution.get("status") != "ok"
        or not resolution.get("mailbox")
    ):
        _send_imap_resolution_error(target, resolution)
        return

    resolved_mailbox = resolution["mailbox"]
    if not _valid_resolved_imap_mailbox(
        resolved_mailbox,
        mailbox_id=mailbox_id,
    ):
        _send_imap_resolution_error(
            target,
            {
                "error": {
                    "code": "mailbox_configuration_malformed",
                }
            },
        )
        return
    imap = resolved_mailbox["imap"]
    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            host=imap["host"],
            port=imap["port"],
            username=imap["username"],
            password=imap["password"],
            ssl_enabled=imap["ssl"],
        )
        trash_folder, discovery_error = imap_trash.discover_trash_folder(
            mailbox
        )
        if discovery_error is not None or trash_folder is None:
            if discovery_error == "trash_folder_ambiguous":
                send_json(
                    target,
                    409,
                    error_payload(
                        "trash_folder_ambiguous",
                        "The Trash mailbox is ambiguous.",
                    ),
                )
            else:
                send_json(
                    target,
                    409,
                    error_payload(
                        "trash_folder_unavailable",
                        "No safe Trash mailbox is available.",
                    ),
                )
            return

        result = read_imap_folder_snapshot(
            mailbox,
            folder=trash_folder,
            mailbox_key=mailbox_id,
            email_address=resolved_mailbox["email"],
            limit=TRASH_FETCH_LIMIT,
            readonly=True,
        )
        if (
            type(result) is not dict
            or set(result)
            != {"ok", "status", "snapshot", "identities", "error"}
            or result.get("ok") is not True
            or result.get("status") != "ok"
            or type(result.get("identities")) is not dict
            or result.get("error") is not None
        ):
            _send_imap_trash_snapshot_failed(target)
            return
        snapshot = result.get("snapshot")
        success_payload = _imap_trash_success_payload(
            mailbox_id=mailbox_id,
            trash_folder=trash_folder,
            snapshot=snapshot,
        )
        if success_payload is None:
            _send_imap_trash_snapshot_failed(target)
            return
        send_json(target, 200, success_payload)
    except imaplib.IMAP4.error:
        send_json(
            target,
            401,
            error_payload(
                "invalid_credentials",
                "Stored IMAP credentials were rejected.",
            ),
        )
    except Exception:
        if mailbox is None:
            send_json(
                target,
                502,
                error_payload(
                    "imap_connection_failed",
                    "A secure IMAP connection could not be established.",
                ),
            )
        else:
            _send_imap_trash_snapshot_failed(target)
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                try:
                    mailbox.shutdown()
                except Exception:
                    pass


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

        owned = resolve_owned_mailbox(
            self.headers,
            payload["mailboxId"],
        )
        if owned["status"] != "ok":
            send_json(
                self,
                owned["status_code"],
                owned["error"],
            )
            return
        provider = owned["inbox"].get("provider")
        if provider == "google":
            _perform_gmail_trash_snapshot(self, owned=owned)
            return
        if provider == "custom_imap":
            _perform_imap_trash_snapshot(
                self,
                mailbox_id=payload["mailboxId"],
            )
            return
        send_json(
            self,
            400,
            error_payload(
                "unsupported_provider",
                "This mailbox is not a Gmail connection.",
            ),
        )

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
