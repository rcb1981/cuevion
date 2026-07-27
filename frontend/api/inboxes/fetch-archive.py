import imaplib
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
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

from authenticated_gmail import (  # noqa: E402
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
from authenticated_imap import (  # noqa: E402
    find_forbidden_custom_request_fields,
    resolve_authenticated_imap_mailbox,
)
from api.inboxes.gmail_snapshot import (  # noqa: E402
    GMAIL_API_UID_VALIDITY,
    read_gmail_folder_snapshot,
)
from api.inboxes.imap_archive import discover_archive_folder  # noqa: E402
from api.inboxes.imap_snapshot import read_imap_folder_snapshot  # noqa: E402
from api.inboxes.imap_uid_validity import is_canonical_uid_validity  # noqa: E402
from imap_connect_preview import connect_mailbox_with_settings  # noqa: E402


GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
ARCHIVE_SNAPSHOT_LIMIT = 100
MAX_ARCHIVE_RESPONSE_BYTES = MAX_GMAIL_RESPONSE_BYTES
MAX_IMAP_UID_SET_SIZE = 100_000
_MAX_IMAP_UID = 4_294_967_295
_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_GMAIL_ARCHIVE_EXCLUDED_LABELS = {
    "INBOX",
    "TRASH",
    "SPAM",
    "DRAFT",
    "SENT",
}
_FORBIDDEN_PUBLIC_KEYS = {
    "accesstoken",
    "authorization",
    "cookie",
    "connection",
    "credentialgeneration",
    "credentialversion",
    "fingerprint",
    "host",
    "identities",
    "identity",
    "mailboxconfig",
    "owneremail",
    "password",
    "port",
    "providererror",
    "rawproviderresponse",
    "refreshtoken",
    "session",
    "secretgeneration",
    "secretversion",
    "ssl",
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


def _compact_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_forbidden_public_key(value: str) -> bool:
    compact = _compact_key(value)
    return compact in _FORBIDDEN_PUBLIC_KEYS or any(
        fragment in compact
        for fragment in _FORBIDDEN_PUBLIC_KEY_FRAGMENTS
    )


def _contains_forbidden_public_fields(value: object) -> bool:
    if type(value) is dict:
        for key, item in value.items():
            if (
                type(key) is not str
                or _is_forbidden_public_key(key)
                or _contains_forbidden_public_fields(item)
            ):
                return True
        return False
    if type(value) is list:
        return any(_contains_forbidden_public_fields(item) for item in value)
    return False


def _archive_success_payload(
    *,
    mailbox_id: str,
    snapshot: dict,
) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "mailboxId": mailbox_id,
        "folder": snapshot,
    }


def _success_response_fits_bound(
    *,
    mailbox_id: str,
    snapshot: dict,
) -> bool:
    try:
        return (
            len(
                json.dumps(
                    _archive_success_payload(
                        mailbox_id=mailbox_id,
                        snapshot=snapshot,
                    )
                ).encode("utf-8")
            )
            <= MAX_ARCHIVE_RESPONSE_BYTES
        )
    except (TypeError, UnicodeEncodeError, ValueError):
        return False


def _valid_imap_uid(value: object) -> bool:
    if type(value) is not str or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _valid_public_text(value: object, *, maximum_length: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum_length
        and value == value.strip()
        and not any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


def _valid_gmail_archive_snapshot(snapshot: object, mailbox_id: str) -> bool:
    if (
        type(snapshot) is not dict
        or set(snapshot)
        != {
            "serverMailboxId",
            "providerFolder",
            "uidValidity",
            "messages",
        }
        or snapshot.get("serverMailboxId") != mailbox_id
        or snapshot.get("providerFolder") != "Archive"
        or snapshot.get("uidValidity") != GMAIL_API_UID_VALIDITY
        or type(snapshot.get("messages")) is not list
        or len(snapshot["messages"]) > ARCHIVE_SNAPSHOT_LIMIT
        or _contains_forbidden_public_fields(snapshot)
        or not _success_response_fits_bound(
            mailbox_id=mailbox_id,
            snapshot=snapshot,
        )
    ):
        return False

    provider_message_ids: set[str] = set()
    for message in snapshot["messages"]:
        if (
            type(message) is not dict
            or message.get("serverMailboxId") != mailbox_id
            or message.get("providerFolder") != "Archive"
            or not valid_identifier(message.get("providerMessageId"))
            or not valid_identifier(message.get("providerThreadId"))
            or message["providerMessageId"] in provider_message_ids
            or "imapUid" in message
        ):
            return False
        provider_message_ids.add(message["providerMessageId"])
        label_ids = message.get("labelIds")
        if (
            type(label_ids) is not list
            or any(not valid_identifier(label_id) for label_id in label_ids)
            or len(set(label_ids)) != len(label_ids)
            or {
                label_id.upper()
                for label_id in label_ids
            }.intersection(_GMAIL_ARCHIVE_EXCLUDED_LABELS)
        ):
            return False
    return True


def _valid_imap_archive_snapshot(
    snapshot: object,
    *,
    mailbox_id: str,
    archive_folder: str,
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
        or snapshot.get("providerFolder") != archive_folder
        or not is_canonical_uid_validity(snapshot.get("uidValidity"))
        or type(snapshot.get("imapUidSet")) is not list
        or len(snapshot["imapUidSet"]) > MAX_IMAP_UID_SET_SIZE
        or type(snapshot.get("messages")) is not list
        or len(snapshot["messages"]) > ARCHIVE_SNAPSHOT_LIMIT
        or _contains_forbidden_public_fields(snapshot)
        or not _success_response_fits_bound(
            mailbox_id=mailbox_id,
            snapshot=snapshot,
        )
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
    message_uids: set[str] = set()
    for message in snapshot["messages"]:
        if (
            type(message) is not dict
            or message.get("serverMailboxId") != mailbox_id
            or message.get("providerFolder") != archive_folder
            or message.get("uidValidity") != uid_validity
            or not _valid_imap_uid(message.get("imapUid"))
            or message["imapUid"] not in known_uids
            or message["imapUid"] in message_uids
            or not _valid_public_text(
                message.get("threadId"),
                maximum_length=512,
            )
        ):
            return False
        message_uids.add(message["imapUid"])
    return True


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
            if type(payload) is not dict:
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
    payload, error = _gmail_request(context["access_token"], path)
    if (
        error
        and error.get("code") == "gmail_token_invalid"
        and not context["refresh_attempted"]
    ):
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, error, context, refreshed
        context = refreshed["context"]
        payload, error = _gmail_request(context["access_token"], path)
    return payload, error, context, None


def _send_gmail_error(handler: BaseHTTPRequestHandler, error: dict):
    code = error.get("code")
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
        (
            502,
            "gmail_fetch_failed",
            "Gmail Archive could not be loaded.",
        ),
    )
    send_json(handler, status, error_payload(safe_code, message))


def _send_imap_resolution_error(
    handler: BaseHTTPRequestHandler,
    resolution: dict,
):
    error = resolution.get("error") or {
        "code": "mailbox_configuration_malformed",
        "message": "Mailbox configuration is invalid.",
        "status_code": 500,
    }
    send_json(
        handler,
        error["status_code"],
        error_payload(error["code"], error["message"]),
    )


def _send_archive_snapshot_failed(handler: BaseHTTPRequestHandler):
    send_json(
        handler,
        502,
        error_payload(
            "archive_snapshot_failed",
            "The Archive mailbox could not be loaded safely.",
        ),
    )


def _send_archive_success(
    handler: BaseHTTPRequestHandler,
    *,
    mailbox_id: str,
    snapshot: dict,
):
    send_json(
        handler,
        200,
        _archive_success_payload(
            mailbox_id=mailbox_id,
            snapshot=snapshot,
        ),
    )


def _perform_gmail_archive_snapshot(
    handler: BaseHTTPRequestHandler,
    *,
    owned: dict,
    mailbox_id: str,
):
    resolution = resolve_gmail_context(owned)
    if resolution["status"] != "ok":
        send_json(
            handler,
            resolution["status_code"],
            resolution["error"],
        )
        return
    context = resolution["context"]
    result = read_gmail_folder_snapshot(
        context,
        provider_folder="Archive",
        request_with_one_refresh=_request_with_one_refresh,
        limit=ARCHIVE_SNAPSHOT_LIMIT,
        focus_preferences=None,
        strict=True,
        required_message_id=None,
    )
    refresh_failure = result.get("refresh_failure")
    if refresh_failure:
        send_json(
            handler,
            refresh_failure["status_code"],
            refresh_failure["error"],
        )
        return
    snapshot_error = result.get("error")
    if snapshot_error:
        _send_gmail_error(handler, snapshot_error)
        return
    snapshot = result.get("snapshot")
    if (
        result.get("status") != "ok"
        or not _valid_gmail_archive_snapshot(snapshot, mailbox_id)
    ):
        _send_archive_snapshot_failed(handler)
        return
    _send_archive_success(
        handler,
        mailbox_id=mailbox_id,
        snapshot=snapshot,
    )


def _perform_imap_archive_snapshot(
    handler: BaseHTTPRequestHandler,
    *,
    headers,
    mailbox_id: str,
):
    resolution = resolve_authenticated_imap_mailbox(headers, mailbox_id)
    if resolution["status"] != "ok" or not resolution.get("mailbox"):
        _send_imap_resolution_error(handler, resolution)
        return

    resolved_mailbox = resolution["mailbox"]
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
        archive_folder, discovery_error = discover_archive_folder(mailbox)
        if discovery_error is not None or archive_folder is None:
            if discovery_error == "archive_folder_ambiguous":
                send_json(
                    handler,
                    409,
                    error_payload(
                        "archive_folder_ambiguous",
                        "The Archive mailbox is ambiguous.",
                    ),
                )
            else:
                send_json(
                    handler,
                    409,
                    error_payload(
                        "archive_folder_unavailable",
                        "No safe Archive mailbox is available.",
                    ),
                )
            return

        result = read_imap_folder_snapshot(
            mailbox,
            folder=archive_folder,
            mailbox_key=mailbox_id,
            email_address=resolved_mailbox["email"],
            limit=ARCHIVE_SNAPSHOT_LIMIT,
            readonly=True,
        )
        snapshot = result.get("snapshot")
        if (
            result.get("status") != "ok"
            or not _valid_imap_archive_snapshot(
                snapshot,
                mailbox_id=mailbox_id,
                archive_folder=archive_folder,
            )
        ):
            _send_archive_snapshot_failed(handler)
            return
        _send_archive_success(
            handler,
            mailbox_id=mailbox_id,
            snapshot=snapshot,
        )
    except imaplib.IMAP4.error:
        send_json(
            handler,
            401,
            error_payload(
                "invalid_credentials",
                "Stored IMAP credentials were rejected.",
            ),
        )
    except Exception:
        if mailbox is None:
            send_json(
                handler,
                502,
                error_payload(
                    "imap_connection_failed",
                    "A secure IMAP connection could not be established.",
                ),
            )
        else:
            _send_archive_snapshot_failed(handler)
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
                "Use POST for Archive mailbox fetch.",
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
                    "The Archive request could not be completed.",
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
        if field_error or find_forbidden_custom_request_fields(payload):
            send_json(
                self,
                400,
                error_payload(
                    "invalid_request",
                    "Request contains unsupported fields.",
                ),
            )
            return

        mailbox_id = payload.get("mailboxId")
        if not valid_identifier(mailbox_id):
            send_json(
                self,
                400,
                error_payload(
                    "invalid_request",
                    "A valid mailbox id is required.",
                ),
            )
            return

        owned = resolve_owned_mailbox(self.headers, mailbox_id)
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")
        if provider == "google":
            _perform_gmail_archive_snapshot(
                self,
                owned=owned,
                mailbox_id=mailbox_id,
            )
            return
        if provider == "custom_imap":
            _perform_imap_archive_snapshot(
                self,
                headers=self.headers,
                mailbox_id=mailbox_id,
            )
            return
        send_json(
            self,
            400,
            error_payload(
                "unsupported_provider",
                "This mailbox provider is not supported.",
            ),
        )

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for Archive mailbox fetch.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(
            self,
            "Use POST for Archive mailbox fetch.",
            write_body=False,
        )

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
