import imaplib
import json
import re
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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

from authenticated_imap import (  # noqa: E402
    find_forbidden_custom_request_fields,
    resolve_authenticated_imap_mailbox,
)
from api.inboxes.gmail_snapshot import parse_gmail_message_detail  # noqa: E402
from api.inboxes.imap_archive import archive_imap_message  # noqa: E402
from api.inboxes.imap_snapshot import (  # noqa: E402
    read_imap_folder_snapshot,
    read_imap_message_identity,
)
from api.inboxes.imap_uid_validity import (  # noqa: E402
    is_canonical_uid_validity,
    read_selected_mailbox_uid_validity,
)
from imap_connect_preview import connect_mailbox_with_settings  # noqa: E402
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

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_FULL_MAIL_SCOPE = "https://mail.google.com/"
IMAP_ARCHIVE_READBACK_LIMIT = 100
GMAIL_ARCHIVE_READBACK_DELAYS_SECONDS = (0.25, 0.75)
SUPPORTED_ACTIONS = {
    "mark_read",
    "mark_unread",
    "star",
    "unstar",
    "archive",
    "trash",
}
GMAIL_ACTION_LABELS = {
    "mark_read": {"removeLabelIds": ["UNREAD"]},
    "mark_unread": {"addLabelIds": ["UNREAD"]},
    "star": {"addLabelIds": ["STARRED"]},
    "unstar": {"removeLabelIds": ["STARRED"]},
    "archive": {"removeLabelIds": ["INBOX"]},
}
IMAP_ACTION_FLAGS = {
    "mark_read": ("+FLAGS.SILENT", "\\Seen"),
    "mark_unread": ("-FLAGS.SILENT", "\\Seen"),
    "star": ("+FLAGS.SILENT", "\\Flagged"),
    "unstar": ("-FLAGS.SILENT", "\\Flagged"),
}
_IMAP_UID_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_MAX_IMAP_UID = 4_294_967_295
_GMAIL_ARCHIVE_FORBIDDEN_PUBLIC_KEYS = {
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
    "providerdetails",
    "providererror",
    "raw",
    "rawproviderresponse",
    "refreshtoken",
    "session",
    "secretgeneration",
    "secretversion",
    "ssl",
    "userid",
    "username",
}
_GMAIL_ARCHIVE_FORBIDDEN_PUBLIC_KEY_FRAGMENTS = {
    "credential",
    "fingerprint",
    "password",
    "secret",
    "token",
}


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict):
    response_body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(response_body)))
    handler.end_headers()
    handler.wfile.write(response_body)


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _has_unsafe_auth_chars(value: str) -> bool:
    return "\r" in value or "\n" in value


def _token_record_has_known_modify_scope(token_record: dict) -> bool | None:
    scope_value = token_record.get("scope")
    if not isinstance(scope_value, str) or not scope_value.strip():
        return None

    scopes = set(scope_value.split())
    return GMAIL_MODIFY_SCOPE in scopes or GMAIL_FULL_MAIL_SCOPE in scopes


def _valid_gmail_archive_message_id(value: object) -> bool:
    if not valid_identifier(value) or not isinstance(value, str) or not value.isascii():
        return False
    lowered = value.casefold()
    return (
        "@" not in value
        and "<" not in value
        and ">" not in value
        and not lowered.startswith(("imap-uid-", "rfc-", "thread-"))
    )


def _valid_archive_imap_uid(value: object) -> bool:
    if not isinstance(value, str) or _IMAP_UID_PATTERN.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_UID)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _contains_forbidden_gmail_archive_public_fields(
    value: object,
) -> bool:
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
                compact_key in _GMAIL_ARCHIVE_FORBIDDEN_PUBLIC_KEYS
                or any(
                    fragment in compact_key
                    for fragment in (
                        _GMAIL_ARCHIVE_FORBIDDEN_PUBLIC_KEY_FRAGMENTS
                    )
                )
                or _contains_forbidden_gmail_archive_public_fields(item)
            ):
                return True
        return False
    if type(value) is list:
        return any(
            _contains_forbidden_gmail_archive_public_fields(item)
            for item in value
        )
    return False


def _gmail_archive_success_payload(
    *,
    mailbox_id: str,
    message_id: str,
    archived_message: dict,
) -> dict:
    archived_identity = {
        "serverMailboxId": mailbox_id,
        "providerMessageId": message_id,
        "providerThreadId": archived_message["providerThreadId"],
        "providerFolder": "Archive",
        **(
            {"rfcMessageId": archived_message["rfcMessageId"]}
            if isinstance(archived_message.get("rfcMessageId"), str)
            else {}
        ),
    }
    return {
        "ok": True,
        "status": "ok",
        "action": "archive",
        "mailboxId": mailbox_id,
        "archivedMessageIdentity": archived_identity,
        "delta": {
            "Inbox": {
                "removeProviderMessageId": message_id,
            },
            "Archive": {
                "upsertMessage": archived_message,
            },
        },
    }


def _gmail_archive_success_payload_is_safe(payload: object) -> bool:
    if (
        type(payload) is not dict
        or _contains_forbidden_gmail_archive_public_fields(payload)
    ):
        return False
    try:
        return (
            len(json.dumps(payload).encode("utf-8"))
            <= MAX_GMAIL_RESPONSE_BYTES
        )
    except (TypeError, UnicodeEncodeError, ValueError):
        return False


def _gmail_modify_request(
    access_token: str,
    message_id: str,
    action: str,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/{quote(message_id, safe='')}/modify",
        data=json.dumps(GMAIL_ACTION_LABELS[action]).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
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
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_message_action_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _gmail_get_request(
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
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
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


def _gmail_get_with_one_refresh(context: dict, path: str):
    payload, request_error = _gmail_get_request(context["access_token"], path)
    if (
        request_error
        and request_error.get("code") == "gmail_token_invalid"
        and not context["refresh_attempted"]
    ):
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, request_error, context, refreshed
        context = refreshed["context"]
        payload, request_error = _gmail_get_request(context["access_token"], path)
    return payload, request_error, context, None


def _gmail_trash_request(
    access_token: str,
    message_id: str,
) -> tuple[dict | None, dict | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/{quote(message_id, safe='')}/trash",
        data=b"",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
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
        if error.code >= 500:
            return None, {"code": "gmail_trash_unavailable"}
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_trash_failed",
            )
        }
    except (URLError, TimeoutError):
        return None, {"code": "gmail_trash_unavailable"}


def _gmail_trash_message_state(
    response_payload: object,
    message_id: str,
) -> str:
    if (
        not isinstance(response_payload, dict)
        or response_payload.get("id") != message_id
    ):
        return "invalid"

    label_ids = response_payload.get("labelIds")
    if (
        not isinstance(label_ids, list)
        or not all(valid_identifier(label_id) for label_id in label_ids)
        or len(set(label_ids)) != len(label_ids)
    ):
        return "invalid"

    labels = set(label_ids)
    if "INBOX" in labels and "TRASH" not in labels:
        return "source"
    if "TRASH" in labels and "INBOX" not in labels:
        return "destination"
    return "other"


def _send_gmail_trash_source_error(
    handler: BaseHTTPRequestHandler,
    request_error: dict,
):
    code = request_error.get("code")
    if code == "gmail_token_invalid":
        send_json(
            handler,
            401,
            error_payload(
                "reconnect_required",
                "Reconnect this Gmail inbox to continue.",
            ),
        )
    elif code == "gmail_permission_denied":
        send_json(
            handler,
            403,
            error_payload(
                "gmail_permission_denied",
                "Gmail did not permit the Trash source read.",
            ),
        )
    elif code == "gmail_rate_limited":
        send_json(
            handler,
            502,
            error_payload(
                "gmail_rate_limited",
                "Gmail is temporarily rate limited.",
            ),
        )
    else:
        send_json(
            handler,
            502,
            error_payload(
                "trash_source_unconfirmed",
                "The Gmail source message could not be verified safely.",
            ),
        )


def _send_gmail_trash_mutation_failure(
    handler: BaseHTTPRequestHandler,
    request_error: dict,
):
    code = request_error.get("code")
    if code == "gmail_token_invalid":
        send_json(
            handler,
            401,
            error_payload(
                "reconnect_required",
                "Reconnect this Gmail inbox to continue.",
            ),
        )
    elif code == "gmail_permission_denied":
        send_json(
            handler,
            403,
            error_payload(
                "gmail_permission_denied",
                "Gmail did not permit this Trash action.",
            ),
        )
    elif code == "gmail_rate_limited":
        send_json(
            handler,
            502,
            error_payload(
                "gmail_rate_limited",
                "Gmail is temporarily rate limited.",
            ),
        )
    else:
        send_json(
            handler,
            502,
            error_payload(
                "gmail_trash_failed",
                "Gmail did not accept this Trash action.",
            ),
        )


def _send_gmail_trash_unconfirmed(
    handler: BaseHTTPRequestHandler,
    *,
    mailbox_id: str,
    message_id: str,
):
    send_json(
        handler,
        502,
        {
            "ok": False,
            "status": "mutation_unconfirmed",
            "action": "trash",
            "provider": "gmail",
            "mailboxId": mailbox_id,
            "providerMessageId": message_id,
            "sourceFolder": "INBOX",
            "destinationFolder": "TRASH",
            "error": {
                "code": "trash_mutation_unconfirmed",
                "message": (
                    "Trash may have completed; the current Gmail state could "
                    "not be confirmed safely."
                ),
            },
        },
    )


def _perform_gmail_trash(
    handler: BaseHTTPRequestHandler,
    payload: dict,
    context: dict,
):
    message_id = payload.get("providerMessageId")
    mailbox_id = context["mailbox_id"]
    if (
        payload.get("sourceFolder") != "INBOX"
        or not _valid_gmail_archive_message_id(message_id)
    ):
        send_json(
            handler,
            400,
            error_payload(
                "invalid_trash_request",
                "Trash requires one concrete Gmail INBOX message identity.",
            ),
        )
        return

    if _token_record_has_known_modify_scope(context) is not True:
        send_json(
            handler,
            403,
            error_payload(
                "gmail_modify_scope_required",
                "Reconnect Gmail with permission to modify messages.",
            ),
        )
        return

    detail_path = (
        f"/messages/{quote(message_id, safe='')}"
        "?format=minimal"
    )
    try:
        source_payload, source_error = _gmail_get_request(
            context["access_token"],
            detail_path,
        )
    except Exception:
        source_payload = None
        source_error = {"code": "gmail_unavailable"}
    if source_error is not None:
        _send_gmail_trash_source_error(handler, source_error)
        return

    source_state = _gmail_trash_message_state(source_payload, message_id)
    if source_state == "invalid":
        send_json(
            handler,
            502,
            error_payload(
                "trash_source_unconfirmed",
                "The Gmail source message response was invalid.",
            ),
        )
        return
    if source_state != "source":
        send_json(
            handler,
            409,
            error_payload(
                "trash_source_invalid",
                "The Gmail message is not an eligible INBOX message.",
            ),
        )
        return

    try:
        mutation_payload, mutation_error = _gmail_trash_request(
            context["access_token"],
            message_id,
        )
    except Exception:
        _send_gmail_trash_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
            message_id=message_id,
        )
        return
    if mutation_error is not None:
        if mutation_error.get("code") in {
            "gmail_trash_unavailable",
            "gmail_response_invalid",
            "gmail_response_too_large",
        }:
            _send_gmail_trash_unconfirmed(
                handler,
                mailbox_id=mailbox_id,
                message_id=message_id,
            )
        else:
            _send_gmail_trash_mutation_failure(handler, mutation_error)
        return
    if _gmail_trash_message_state(mutation_payload, message_id) == "invalid":
        _send_gmail_trash_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
            message_id=message_id,
        )
        return

    try:
        readback_payload, readback_error = _gmail_get_request(
            context["access_token"],
            detail_path,
        )
    except Exception:
        _send_gmail_trash_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
            message_id=message_id,
        )
        return
    if (
        readback_error is not None
        or _gmail_trash_message_state(readback_payload, message_id)
        != "destination"
    ):
        _send_gmail_trash_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
            message_id=message_id,
        )
        return

    send_json(
        handler,
        200,
        {
            "ok": True,
            "action": "trash",
            "provider": "gmail",
            "mailboxId": mailbox_id,
            "providerMessageId": message_id,
            "sourceFolder": "INBOX",
            "destinationFolder": "TRASH",
            "readback": {
                "inSource": False,
                "inTrash": True,
            },
        },
    )


def _perform_gmail_action(handler: BaseHTTPRequestHandler, payload: dict, action: str, context: dict):
    message_id = payload.get("messageId")
    if not valid_identifier(message_id):
        send_json(handler, 400, error_payload("invalid_request", "Message id is invalid."))
        return

    _, modify_error = _gmail_modify_request(context["access_token"], message_id, action)
    if modify_error and modify_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            send_json(handler, refreshed["status_code"], refreshed["error"])
            return
        context = refreshed["context"]
        _, modify_error = _gmail_modify_request(context["access_token"], message_id, action)

    if modify_error:
        code = modify_error.get("code")
        if code == "gmail_token_invalid":
            send_json(handler, 401, error_payload("reconnect_required", "Reconnect this Gmail inbox to continue."))
        elif code == "gmail_permission_denied":
            send_json(handler, 403, error_payload("gmail_permission_denied", "Gmail did not permit this mailbox action."))
        elif code == "gmail_rate_limited":
            send_json(handler, 502, error_payload("gmail_rate_limited", "Gmail is temporarily rate limited."))
        elif code == "gmail_unavailable":
            send_json(handler, 502, error_payload("gmail_unavailable", "Gmail is temporarily unavailable."))
        else:
            send_json(handler, 502, error_payload("gmail_message_action_failed", "Gmail could not update this message."))
        return

    send_json(handler, 200, {"ok": True, "action": action})


def _gmail_archive_modify_response_has_expected_identity(
    response_payload: object,
    message_id: str,
) -> bool:
    return (
        isinstance(response_payload, dict)
        and response_payload.get("id") == message_id
    )


def _gmail_archive_readback_confirmation_state(
    response_payload: object,
    message_id: str,
) -> str:
    if (
        not isinstance(response_payload, dict)
        or response_payload.get("id") != message_id
    ):
        return "invalid"
    label_ids = response_payload.get("labelIds", [])
    if (
        not isinstance(label_ids, list)
        or any(not valid_identifier(label_id) for label_id in label_ids)
        or len(set(label_ids)) != len(label_ids)
    ):
        return "invalid"
    return "pending" if "INBOX" in label_ids else "confirmed"


def _gmail_archive_readback_sleep(delay_seconds: float):
    time.sleep(delay_seconds)


def _send_gmail_archive_unconfirmed(
    handler: BaseHTTPRequestHandler,
    *,
    mailbox_id: str,
):
    send_json(
        handler,
        502,
        {
            "ok": False,
            "status": "mutation_unconfirmed",
            "action": "archive",
            "mailboxId": mailbox_id,
            "error": {
                "code": "gmail_archive_unconfirmed",
                "message": (
                    "Archive may have completed; mailbox status is being "
                    "refreshed."
                ),
            },
        },
    )


def _send_gmail_archive_transport_error(
    handler: BaseHTTPRequestHandler,
    error: dict,
    *,
    mailbox_id: str,
):
    code = error.get("code")
    if code == "gmail_token_invalid":
        send_json(
            handler,
            401,
            error_payload(
                "reconnect_required",
                "Reconnect this Gmail inbox to continue.",
            ),
        )
    elif code == "gmail_permission_denied":
        send_json(
            handler,
            403,
            error_payload(
                "gmail_archive_failed",
                "Gmail did not permit this Archive action.",
            ),
        )
    elif code == "gmail_rate_limited":
        send_json(
            handler,
            502,
            error_payload(
                "gmail_rate_limited",
                "Gmail is temporarily rate limited.",
            ),
        )
    elif code == "gmail_unavailable":
        _send_gmail_archive_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
        )
    elif code in {"gmail_response_invalid", "gmail_response_too_large"}:
        _send_gmail_archive_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
        )
    else:
        send_json(
            handler,
            502,
            error_payload(
                "gmail_archive_failed",
                "Gmail could not archive this message.",
            ),
        )


def _send_archive_readback_failed(
    handler: BaseHTTPRequestHandler,
    *,
    mailbox_id: str,
    archived_message_identity: dict,
):
    send_json(
        handler,
        502,
        {
            "ok": False,
            "status": "mutation_confirmed_readback_failed",
            "action": "archive",
            "mailboxId": mailbox_id,
            "archivedMessageIdentity": archived_message_identity,
            "error": {
                "code": "archive_readback_failed",
                "message": (
                    "Archive was confirmed, but the latest mailbox state "
                    "could not be verified."
                ),
            },
        },
    )


def _perform_gmail_archive(
    handler: BaseHTTPRequestHandler,
    payload: dict,
    context: dict,
):
    message_id = payload.get("messageId")
    mailbox_id = context["mailbox_id"]
    if not _valid_gmail_archive_message_id(message_id):
        send_json(
            handler,
            400,
            error_payload(
                "invalid_request",
                "A concrete Gmail provider message id is required.",
            ),
        )
        return

    if _token_record_has_known_modify_scope(context) is not True:
        send_json(
            handler,
            403,
            error_payload(
                "gmail_modify_scope_required",
                "Reconnect Gmail with permission to modify messages.",
            ),
        )
        return

    modify_payload, modify_error = _gmail_modify_request(
        context["access_token"],
        message_id,
        "archive",
    )

    if modify_error:
        _send_gmail_archive_transport_error(
            handler,
            modify_error,
            mailbox_id=mailbox_id,
        )
        return
    if not _gmail_archive_modify_response_has_expected_identity(
        modify_payload,
        message_id,
    ):
        _send_gmail_archive_unconfirmed(
            handler,
            mailbox_id=mailbox_id,
        )
        return

    mutation_identity = {
        "serverMailboxId": mailbox_id,
        "providerMessageId": message_id,
        "providerFolder": "Archive",
    }
    mutation_confirmed = False

    try:
        detail_path = (
            f"/messages/{quote(message_id, safe='')}"
            "?format=raw"
        )
        detail_payload = None
        for attempt in range(
            len(GMAIL_ARCHIVE_READBACK_DELAYS_SECONDS) + 1
        ):
            if attempt > 0:
                _gmail_archive_readback_sleep(
                    GMAIL_ARCHIVE_READBACK_DELAYS_SECONDS[attempt - 1]
                )
            (
                detail_payload,
                detail_error,
                context,
                refresh_failure,
            ) = _gmail_get_with_one_refresh(
                context,
                detail_path,
            )
            if refresh_failure is not None or detail_error is not None:
                _send_gmail_archive_unconfirmed(
                    handler,
                    mailbox_id=mailbox_id,
                )
                return

            confirmation_state = (
                _gmail_archive_readback_confirmation_state(
                    detail_payload,
                    message_id,
                )
            )
            if confirmation_state == "confirmed":
                mutation_confirmed = True
                break
            if confirmation_state == "invalid":
                _send_gmail_archive_unconfirmed(
                    handler,
                    mailbox_id=mailbox_id,
                )
                return
        else:
            _send_gmail_archive_unconfirmed(
                handler,
                mailbox_id=mailbox_id,
            )
            return

        archived_message = parse_gmail_message_detail(
            detail_payload,
            context=context,
            provider_folder="Archive",
            requested_message_id=message_id,
            index=0,
            focus_preferences=None,
            strict=True,
        )
        if archived_message is None:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return

        success_payload = _gmail_archive_success_payload(
            mailbox_id=mailbox_id,
            message_id=message_id,
            archived_message=archived_message,
        )
        if not _gmail_archive_success_payload_is_safe(success_payload):
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return
        send_json(
            handler,
            200,
            success_payload,
        )
    except Exception:
        if mutation_confirmed:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
        else:
            _send_gmail_archive_unconfirmed(
                handler,
                mailbox_id=mailbox_id,
            )


def _read_uid_validity(mailbox, folder: str) -> str | None:
    del folder
    return read_selected_mailbox_uid_validity(mailbox)


def _send_imap_archive_failure(
    handler: BaseHTTPRequestHandler,
    result: dict,
):
    error = result.get("error") if isinstance(result, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    messages = {
        "invalid_source_folder": "The source mailbox folder is invalid.",
        "invalid_imap_uid": "The IMAP message UID is invalid.",
        "invalid_uid_validity": "The IMAP UIDVALIDITY is invalid.",
        "archive_folder_unavailable": "No safe Archive mailbox is available.",
        "archive_folder_ambiguous": "The Archive mailbox is ambiguous.",
        "archive_move_unsupported": "This mailbox does not support safe Archive moves.",
        "source_folder_unavailable": "The source mailbox folder could not be opened.",
        "uid_validity_unavailable": "The mailbox UIDVALIDITY could not be verified.",
        "uid_validity_changed": "This mailbox changed since the message was fetched.",
        "archive_message_not_found": "The source message no longer exists.",
        "archive_move_failed": "The IMAP server rejected the Archive move.",
        "archive_move_unconfirmed": "The IMAP server did not confirm the Archive move.",
        "imap_archive_failed": "The message could not be archived through IMAP.",
    }
    safe_code = code if code in messages else "imap_archive_failed"
    if safe_code in {"invalid_source_folder", "invalid_imap_uid", "invalid_uid_validity"}:
        status_code = 400
    elif safe_code == "archive_message_not_found":
        status_code = 404
    elif safe_code in {
        "archive_folder_unavailable",
        "archive_folder_ambiguous",
        "archive_move_unsupported",
        "source_folder_unavailable",
        "uid_validity_unavailable",
        "uid_validity_changed",
    }:
        status_code = 409
    else:
        status_code = 502
    _json_response(
        handler,
        status_code,
        _error(safe_code, messages[safe_code]),
    )


def _send_imap_source_read_failure(
    handler: BaseHTTPRequestHandler,
    result: dict,
):
    error = result.get("error") if isinstance(result, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code == "uid_validity_changed":
        _json_response(
            handler,
            409,
            _error(
                "uid_validity_changed",
                "This mailbox changed since the message was fetched.",
            ),
        )
    elif code == "message_not_found":
        _json_response(
            handler,
            404,
            _error(
                "archive_message_not_found",
                "The source message no longer exists.",
            ),
        )
    else:
        _json_response(
            handler,
            502,
            _error(
                "imap_archive_failed",
                "The source message could not be verified for Archive.",
            ),
        )


def _imap_archive_result_is_confirmed(
    result: object,
    *,
    source_folder: str,
    uid: str,
    uid_validity: str,
) -> bool:
    return (
        isinstance(result, dict)
        and result.get("ok") is True
        and result.get("status") == "ok"
        and result.get("source_folder") == source_folder
        and isinstance(result.get("archive_folder"), str)
        and bool(result["archive_folder"])
        and result.get("uid") == uid
        and result.get("uid_validity") == uid_validity
        and result.get("confirmation") == "source_removed"
        and result.get("error") is None
    )


def _same_imap_message_identity(left: object, right: object) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_fingerprint = left.get("fingerprint")
    right_fingerprint = right.get("fingerprint")
    if (
        not isinstance(left_fingerprint, str)
        or not left_fingerprint
        or left_fingerprint != right_fingerprint
    ):
        return False
    right_rfc_message_id = right.get("rfcMessageId")
    return (
        right_rfc_message_id is None
        or (
            isinstance(right_rfc_message_id, str)
            and left.get("rfcMessageId") == right_rfc_message_id
        )
    )


def _imap_identity_has_scope(
    identity: object,
    *,
    folder: str,
    uid: str,
    uid_validity: str,
) -> bool:
    return (
        isinstance(identity, dict)
        and identity.get("providerFolder") == folder
        and identity.get("imapUid") == uid
        and identity.get("uidValidity") == uid_validity
    )


def _perform_imap_archive(
    handler: BaseHTTPRequestHandler,
    payload: dict,
):
    mailbox_id = payload.get("mailboxId")
    folder = payload.get("folder")
    uid = payload.get("uid")
    uid_validity = payload.get("uidValidity")

    if folder != "INBOX":
        _json_response(
            handler,
            400,
            _error(
                "unsupported_source_folder",
                "Archive currently supports messages from Inbox only.",
            ),
        )
        return
    if not _valid_archive_imap_uid(uid):
        _json_response(
            handler,
            400,
            _error(
                "missing_imap_uid",
                "IMAP Archive requires one concrete message UID.",
            ),
        )
        return
    if not is_canonical_uid_validity(uid_validity):
        _json_response(
            handler,
            400,
            _error(
                "invalid_request",
                "IMAP Archive requires canonical UIDVALIDITY.",
            ),
        )
        return

    resolved = resolve_authenticated_imap_mailbox(handler.headers, mailbox_id)
    if resolved["status"] != "ok" or not resolved["mailbox"]:
        error = resolved["error"] or {
            "code": "mailbox_configuration_malformed",
            "message": "Mailbox configuration is invalid.",
            "status_code": 500,
        }
        _json_response(
            handler,
            error["status_code"],
            _error(error["code"], error["message"]),
        )
        return

    resolved_mailbox = resolved["mailbox"]
    imap = resolved_mailbox["imap"]
    mailbox = None
    mutation_confirmed = False
    mutation_identity = {
        "serverMailboxId": mailbox_id,
        "sourceProviderFolder": folder,
        "sourceImapUid": uid,
        "sourceUidValidity": uid_validity,
    }
    try:
        mailbox = connect_mailbox_with_settings(
            host=imap["host"],
            port=imap["port"],
            username=imap["username"],
            password=imap["password"],
            ssl_enabled=imap["ssl"],
        )

        source_identity_result = read_imap_message_identity(
            mailbox,
            folder=folder,
            uid=uid,
            expected_uid_validity=uid_validity,
        )
        if source_identity_result.get("status") != "ok":
            _send_imap_source_read_failure(handler, source_identity_result)
            return
        source_identity = source_identity_result.get("identity")
        if not _imap_identity_has_scope(
            source_identity,
            folder=folder,
            uid=uid,
            uid_validity=uid_validity,
        ):
            _send_imap_source_read_failure(handler, {})
            return

        archive_result = archive_imap_message(
            mailbox,
            source_folder=folder,
            uid=uid,
            expected_uid_validity=uid_validity,
        )
        if archive_result.get("ok") is not True:
            _send_imap_archive_failure(handler, archive_result)
            return
        if not _imap_archive_result_is_confirmed(
            archive_result,
            source_folder=folder,
            uid=uid,
            uid_validity=uid_validity,
        ):
            _json_response(
                handler,
                502,
                _error(
                    "archive_move_unconfirmed",
                    "The IMAP server did not confirm the Archive move.",
                ),
            )
            return
        mutation_confirmed = True

        archive_folder = archive_result.get("archive_folder")
        if not isinstance(archive_folder, str) or not archive_folder:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return

        inbox_readback = read_imap_folder_snapshot(
            mailbox,
            folder=folder,
            mailbox_key=mailbox_id,
            email_address=resolved_mailbox["email"],
            limit=IMAP_ARCHIVE_READBACK_LIMIT,
        )
        archive_readback = read_imap_folder_snapshot(
            mailbox,
            folder=archive_folder,
            mailbox_key=mailbox_id,
            email_address=resolved_mailbox["email"],
            limit=IMAP_ARCHIVE_READBACK_LIMIT,
        )
        if (
            inbox_readback.get("status") != "ok"
            or archive_readback.get("status") != "ok"
        ):
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return

        inbox_snapshot = inbox_readback.get("snapshot")
        archive_snapshot = archive_readback.get("snapshot")
        archive_identities = archive_readback.get("identities")
        if (
            not isinstance(inbox_snapshot, dict)
            or not isinstance(archive_snapshot, dict)
            or not isinstance(archive_identities, dict)
            or inbox_snapshot.get("serverMailboxId") != mailbox_id
            or archive_snapshot.get("serverMailboxId") != mailbox_id
            or inbox_snapshot.get("providerFolder") != folder
            or inbox_snapshot.get("uidValidity") != uid_validity
            or not isinstance(inbox_snapshot.get("imapUidSet"), list)
            or archive_snapshot.get("providerFolder") != archive_folder
            or not is_canonical_uid_validity(
                archive_snapshot.get("uidValidity"),
            )
            or not isinstance(archive_snapshot.get("imapUidSet"), list)
            or uid in inbox_snapshot.get("imapUidSet", [])
        ):
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return

        target_uids = [
            candidate_uid
            for candidate_uid, candidate_identity in archive_identities.items()
            if (
                _valid_archive_imap_uid(candidate_uid)
                and _imap_identity_has_scope(
                    candidate_identity,
                    folder=archive_folder,
                    uid=candidate_uid,
                    uid_validity=archive_snapshot["uidValidity"],
                )
                and _same_imap_message_identity(
                    candidate_identity,
                    source_identity,
                )
            )
        ]
        if len(target_uids) != 1:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return
        target_uid = target_uids[0]
        if target_uid not in archive_snapshot["imapUidSet"]:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return
        archived_messages = [
            message
            for message in archive_snapshot.get("messages", [])
            if isinstance(message, dict)
            and message.get("imapUid") == target_uid
        ]
        if len(archived_messages) != 1:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
            return

        archived_message = archived_messages[0]
        archived_identity = {
            **mutation_identity,
            "providerFolder": archive_folder,
            "imapUid": target_uid,
            "uidValidity": archive_snapshot["uidValidity"],
            **(
                {"rfcMessageId": archived_message["rfcMessageId"]}
                if isinstance(archived_message.get("rfcMessageId"), str)
                else {}
            ),
        }
        _json_response(
            handler,
            200,
            {
                "ok": True,
                "status": "ok",
                "action": "archive",
                "mailboxId": mailbox_id,
                "archivedMessageIdentity": archived_identity,
                "folders": {
                    "Inbox": inbox_snapshot,
                    "Archive": archive_snapshot,
                },
            },
        )
    except imaplib.IMAP4.error:
        if mutation_confirmed:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
        else:
            _json_response(
                handler,
                401,
                _error(
                    "invalid_credentials",
                    "Stored IMAP credentials were rejected.",
                ),
            )
    except Exception:
        if mutation_confirmed:
            _send_archive_readback_failed(
                handler,
                mailbox_id=mailbox_id,
                archived_message_identity=mutation_identity,
            )
        else:
            _json_response(
                handler,
                502,
                _error(
                    "imap_archive_failed",
                    "Could not archive this message through IMAP.",
                ),
            )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                try:
                    mailbox.shutdown()
                except Exception:
                    pass


def _perform_imap_action(handler: BaseHTTPRequestHandler, payload: dict, action: str):
    if set(payload) - {"mailboxId", "folder", "uid", "uidValidity", "action"}:
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return
    if find_forbidden_custom_request_fields(payload):
        _json_response(
            handler,
            400,
            _error("forbidden_connection_fields", "Connection details are not accepted."),
        )
        return
    if action == "archive":
        _perform_imap_archive(handler, payload)
        return

    mailbox_id = payload.get("mailboxId")
    folder = payload.get("folder")
    uid = payload.get("uid")
    uid_validity = payload.get("uidValidity")

    if (
        not isinstance(folder, str)
        or not folder
        or folder != folder.strip()
        or "\r" in folder
        or "\n" in folder
    ):
        _json_response(
            handler,
            400,
            _error("missing_folder", "IMAP mailbox actions require the source folder."),
        )
        return

    if not isinstance(uid, str) or not uid.isdigit() or uid == "0":
        _json_response(
            handler,
            400,
            _error("missing_imap_uid", "IMAP mailbox actions require the message UID."),
        )
        return

    if uid_validity is not None and not is_canonical_uid_validity(uid_validity):
        _json_response(
            handler,
            400,
            _error("invalid_request", "IMAP UIDVALIDITY is invalid."),
        )
        return

    resolved = resolve_authenticated_imap_mailbox(handler.headers, mailbox_id)
    if resolved["status"] != "ok" or not resolved["mailbox"]:
        error = resolved["error"] or {
            "code": "mailbox_configuration_malformed",
            "message": "Mailbox configuration is invalid.",
            "status_code": 500,
        }
        _json_response(handler, error["status_code"], _error(error["code"], error["message"]))
        return
    imap = resolved["mailbox"]["imap"]

    mailbox = None
    try:
        mailbox = connect_mailbox_with_settings(
            host=imap["host"],
            port=imap["port"],
            username=imap["username"],
            password=imap["password"],
            ssl_enabled=imap["ssl"],
        )

        select_status, _ = mailbox.select(folder)
        if select_status != "OK":
            _json_response(
                handler,
                404,
                _error("mailbox_not_found", "The source mailbox folder could not be opened."),
            )
            return

        if uid_validity:
            current_uid_validity = _read_uid_validity(mailbox, folder)
            if current_uid_validity != uid_validity:
                _json_response(
                    handler,
                    409,
                    _error("uid_validity_changed", "This mailbox changed since the message was fetched."),
                )
                return

        operation, flag = IMAP_ACTION_FLAGS[action]
        status, _ = mailbox.uid("store", uid, operation, f"({flag})")
        if status != "OK":
            _json_response(
                handler,
                404,
                _error("message_action_failed", "The source message could not be updated."),
            )
            return

        _json_response(handler, 200, {"ok": True, "action": action})
    except imaplib.IMAP4.error:
        _json_response(
            handler,
            401,
            _error("invalid_credentials", "Stored IMAP credentials were rejected."),
        )
    except Exception:
        _json_response(
            handler,
            502,
            _error("imap_message_action_failed", "Could not update this message through IMAP."),
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass


class handler(BaseHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        if code == HTTPStatus.NOT_IMPLEMENTED:
            self.close_connection = True
            send_method_not_allowed(
                self,
                "Use POST for mailbox message actions.",
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
                error_payload("internal_error", "The mailbox action could not be completed."),
            )

    def _handle_post(self):
        payload, error = read_json_body(self)
        if error:
            send_json(self, 413 if error["error"]["code"] == "request_too_large" else 400, error)
            return

        action = payload.get("action")

        if action not in SUPPORTED_ACTIONS:
            send_json(self, 400, error_payload("unsupported_action", "This mailbox action is not supported."))
            return

        if action == "trash":
            trash_fields = {
                "mailboxId",
                "providerMessageId",
                "sourceFolder",
                "action",
            }
            field_error = reject_unknown_fields(
                payload,
                trash_fields,
            )
            if (
                field_error
                or set(payload) != trash_fields
                or not valid_identifier(payload.get("mailboxId"))
                or not _valid_gmail_archive_message_id(
                    payload.get("providerMessageId")
                )
                or payload.get("sourceFolder") != "INBOX"
                or find_forbidden_custom_request_fields(payload)
            ):
                send_json(
                    self,
                    400,
                    error_payload(
                        "invalid_trash_request",
                        "Trash requires one managed mailbox and provider message identity.",
                    ),
                )
                return
        else:
            field_error = reject_unknown_fields(
                payload,
                {"mailboxId", "messageId", "folder", "uid", "uidValidity", "action"},
            )
            if field_error or find_forbidden_custom_request_fields(payload):
                send_json(self, 400, error_payload("forbidden_connection_fields", "Connection and identity details are not accepted."))
                return

        owned = resolve_owned_mailbox(self.headers, payload.get("mailboxId"))
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")
        if provider == "google":
            if action != "trash":
                field_error = reject_unknown_fields(payload, {"mailboxId", "messageId", "action"})
                if field_error:
                    send_json(self, 400, field_error)
                    return
            gmail = resolve_gmail_context(owned)
            if gmail["status"] != "ok":
                send_json(self, gmail["status_code"], gmail["error"])
                return
            if action == "trash":
                _perform_gmail_trash(
                    self,
                    payload,
                    gmail["context"],
                )
                return
            if action == "archive":
                _perform_gmail_archive(
                    self,
                    payload,
                    gmail["context"],
                )
                return
            _perform_gmail_action(self, payload, action, gmail["context"])
            return
        if provider == "custom_imap":
            if action == "trash":
                send_json(
                    self,
                    409,
                    error_payload(
                        "trash_provider_not_supported",
                        "Provider-authoritative Trash is not supported for this mailbox.",
                    ),
                )
                return
            _perform_imap_action(self, payload, action)
            return
        send_json(self, 400, error_payload("unsupported_provider", "Mailbox actions require Gmail or IMAP."))

    def do_GET(self):
        send_method_not_allowed(self, "Use POST for mailbox message actions.")

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()

    def do_HEAD(self):
        send_method_not_allowed(self, "Use POST for mailbox message actions.", write_body=False)

    def do_OPTIONS(self):
        send_json(self, 200, {"ok": True})

    def log_message(self, format, *args):
        return
