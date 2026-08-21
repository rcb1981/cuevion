import base64
import binascii
import imaplib
import json
import re
import sys
import time
from email.errors import HeaderParseError
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from http import HTTPStatus
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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
from smtp_connection import SmtpConnectionError, send_public_smtp_message
from api.inboxes.imap_snapshot import read_imap_reply_source
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
    valid_identifier,
)
from api.priority.authority import (
    PriorityAuthority,
    mint_outgoing_event_reference_for_authority,
    priority_authority_from_owned_mailbox,
)
from api.priority.event_reference import resolve_priority_hmac_secret
from api.priority.semantic_config import SemanticMode, load_semantic_runtime_config
from imap_connect_preview import connect_mailbox_with_settings

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_ATTACHMENTS = 10
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_RECIPIENTS = 100
MAX_SUBJECT_CHARACTERS = 998
MAX_BODY_CHARACTERS = 2 * 1024 * 1024
MAX_GMAIL_METADATA_HEADERS = 64
MAX_GMAIL_METADATA_HEADER_NAME_CHARACTERS = 64
MAX_GMAIL_METADATA_HEADER_VALUE_CHARACTERS = 16 * 1024
MAX_GMAIL_METADATA_TOTAL_CHARACTERS = 32 * 1024
MAX_RFC_MESSAGE_ID_CHARACTERS = 998
MAX_REPLY_REFERENCE_TOKENS = 32
MAX_REPLY_REFERENCES_CHARACTERS = 4096
MAX_GMAIL_SEND_LABELS = 100
GMAIL_REPLY_METADATA_HEADERS = (
    "Message-ID",
    "References",
    "In-Reply-To",
    "Subject",
)
_RFC_MESSAGE_ID_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_RFC_MESSAGE_ID_LOCAL_PART = rf"{_RFC_MESSAGE_ID_ATOM}(?:\.{_RFC_MESSAGE_ID_ATOM})*"
_RFC_MESSAGE_ID_RIGHT_DOT_ATOM = _RFC_MESSAGE_ID_LOCAL_PART
_RFC_MESSAGE_ID_NO_FOLD_LITERAL = r"\[[\x21-\x5a\x5e-\x7e]+\]"
_RFC_MESSAGE_ID_RIGHT = (
    rf"(?:{_RFC_MESSAGE_ID_RIGHT_DOT_ATOM}|{_RFC_MESSAGE_ID_NO_FOLD_LITERAL})"
)
_RFC_MESSAGE_ID_INNER_PATTERN = re.compile(
    rf"{_RFC_MESSAGE_ID_LOCAL_PART}@{_RFC_MESSAGE_ID_RIGHT}"
)
_RFC_MESSAGE_ID_TOKEN_PATTERN = re.compile(
    rf"<{_RFC_MESSAGE_ID_LOCAL_PART}@{_RFC_MESSAGE_ID_RIGHT}>"
)
_REPLY_SUBJECT_PREFIX_PATTERN = re.compile(r"^re:\s*", re.IGNORECASE)
_CANONICAL_IMAP_NUMBER_PATTERN = re.compile(r"[1-9][0-9]*", re.ASCII)
_MAX_IMAP_NUMBER = 4_294_967_295
_MAX_IMAP_FOLDER_BYTES = 16_384


class _CustomImapAuthenticationError(Exception):
    pass


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


def _valid_gmail_identifier(value: object) -> bool:
    return valid_identifier(value) and isinstance(value, str) and value.isascii()


def _validate_reply_context(payload: dict) -> tuple[dict | None, dict | None]:
    if "replyContext" not in payload:
        return None, None

    reply_context = payload.get("replyContext")
    if (
        type(reply_context) is not dict
        or set(reply_context) != {"sourceProviderMessageId"}
        or not _valid_gmail_identifier(
            reply_context.get("sourceProviderMessageId")
        )
    ):
        return None, error_payload(
            "invalid_reply_context",
            "Reply context must identify exactly one valid Gmail source message.",
        )

    return {
        "sourceProviderMessageId": reply_context["sourceProviderMessageId"]
    }, None


def _valid_imap_context_number(value: object) -> bool:
    if (
        type(value) is not str
        or _CANONICAL_IMAP_NUMBER_PATTERN.fullmatch(value) is None
    ):
        return False
    maximum = str(_MAX_IMAP_NUMBER)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _valid_imap_context_folder(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= _MAX_IMAP_FOLDER_BYTES
    except UnicodeEncodeError:
        return False


def _validate_imap_reply_context(
    payload: dict,
) -> tuple[dict | None, dict | None]:
    if "imapReplyContext" not in payload:
        return None, None

    reply_context = payload.get("imapReplyContext")
    if (
        type(reply_context) is not dict
        or set(reply_context)
        != {
            "sourceProviderFolder",
            "sourceImapUid",
            "sourceUidValidity",
        }
        or not _valid_imap_context_folder(
            reply_context.get("sourceProviderFolder")
        )
        or not _valid_imap_context_number(reply_context.get("sourceImapUid"))
        or not _valid_imap_context_number(
            reply_context.get("sourceUidValidity")
        )
    ):
        return None, error_payload(
            "invalid_imap_reply_context",
            "IMAP reply context must identify exactly one valid source message.",
        )

    return {
        "sourceProviderFolder": reply_context["sourceProviderFolder"],
        "sourceImapUid": reply_context["sourceImapUid"],
        "sourceUidValidity": reply_context["sourceUidValidity"],
    }, None


def _normalize_rfc_message_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > MAX_RFC_MESSAGE_ID_CHARACTERS
        or not normalized.isascii()
        or _has_unsafe_header_chars(normalized)
    ):
        return None
    if not normalized.startswith("<") or not normalized.endswith(">"):
        return None
    inner = normalized[1:-1]
    if _RFC_MESSAGE_ID_INNER_PATTERN.fullmatch(inner) is None:
        return None
    return f"<{inner}>"


def _normalize_custom_rfc_message_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > MAX_RFC_MESSAGE_ID_CHARACTERS
        or not normalized.isascii()
        or _has_unsafe_header_chars(normalized)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or not normalized.startswith("<")
        or not normalized.endswith(">")
    ):
        return None
    inner = normalized[1:-1]
    if not inner:
        return None

    if inner.startswith('"'):
        index = 1
        while index < len(inner):
            character = inner[index]
            if character == "\\":
                index += 2
                continue
            if character == '"':
                break
            index += 1
        if (
            index >= len(inner)
            or index + 1 >= len(inner)
            or inner[index + 1] != "@"
        ):
            return None
        left = inner[: index + 1]
        right = inner[index + 2 :]
    else:
        separator_index = inner.find("@")
        if separator_index < 0:
            return None
        left = inner[:separator_index]
        right = inner[separator_index + 1 :]

    if not left or not right:
        return None
    valid_left = re.fullmatch(_RFC_MESSAGE_ID_LOCAL_PART, left) is not None
    if not valid_left and len(left) >= 2 and left[0] == '"' and left[-1] == '"':
        index = 1
        valid_left = True
        while index < len(left) - 1:
            character = left[index]
            if character == "\\":
                index += 1
                if index >= len(left) - 1:
                    valid_left = False
                    break
                character = left[index]
            elif character == '"':
                valid_left = False
                break
            if ord(character) < 32 or ord(character) > 126:
                valid_left = False
                break
            index += 1
    valid_right = re.fullmatch(_RFC_MESSAGE_ID_RIGHT_DOT_ATOM, right) is not None
    if not valid_right and right.startswith("[") and right.endswith("]"):
        literal = right[1:-1]
        valid_right = not any(
            ord(character) < 33
            or ord(character) > 126
            or character in "[\\]"
            for character in literal
        )
    if not valid_left or not valid_right:
        return None
    return f"<{inner}>"


def _parse_rfc_message_id_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _RFC_MESSAGE_ID_TOKEN_PATTERN.finditer(value):
        token = _normalize_rfc_message_id(match.group(0))
        if token is None or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= MAX_REPLY_REFERENCE_TOKENS:
            break
    return tokens


def _build_reply_references(raw_references: str, source_message_id: str) -> str:
    historic_tokens = _parse_rfc_message_id_tokens(raw_references)
    selected: list[str] = []
    selected_characters = 0
    maximum_historic_tokens = MAX_REPLY_REFERENCE_TOKENS - 1

    for token in historic_tokens:
        if token == source_message_id or len(selected) >= maximum_historic_tokens:
            continue
        separator_characters = 1 if selected else 0
        reserved_source_characters = 1 + len(source_message_id)
        if (
            selected_characters
            + separator_characters
            + len(token)
            + reserved_source_characters
            > MAX_REPLY_REFERENCES_CHARACTERS
        ):
            break
        selected.append(token)
        selected_characters += separator_characters + len(token)

    selected.append(source_message_id)
    return " ".join(selected)


def _build_custom_reply_references(
    raw_references: object,
    raw_in_reply_to: object,
    source_message_id: str,
) -> str:
    historic_tokens: list[str] = []
    seen: set[str] = set()

    if type(raw_references) is list:
        for value in raw_references:
            token = _normalize_custom_rfc_message_id(value)
            if token is None or token == source_message_id or token in seen:
                continue
            seen.add(token)
            historic_tokens.append(token)

    if type(raw_references) is list and not raw_references:
        fallback_parent = _normalize_custom_rfc_message_id(raw_in_reply_to)
        if fallback_parent is not None and fallback_parent != source_message_id:
            historic_tokens.append(fallback_parent)

    maximum_historic_tokens = MAX_REPLY_REFERENCE_TOKENS - 1
    if len(historic_tokens) > maximum_historic_tokens:
        selected = [
            historic_tokens[0],
            *historic_tokens[-(maximum_historic_tokens - 1) :],
        ]
    else:
        selected = list(historic_tokens)

    while (
        len(" ".join([*selected, source_message_id]))
        > MAX_REPLY_REFERENCES_CHARACTERS
        and len(selected) > 1
    ):
        selected.pop(1)

    selected.append(source_message_id)
    return " ".join(selected)


def _validate_custom_reply_source(
    result: object,
    context: dict,
) -> tuple[dict | None, tuple[int, dict] | None]:
    if type(result) is not dict:
        return None, _imap_reply_source_error_response(
            {"code": "message_identity_unconfirmed"}
        )

    if result.get("ok") is not True or result.get("status") != "ok":
        raw_error = result.get("error")
        error = raw_error if type(raw_error) is dict else {}
        return None, _imap_reply_source_error_response(error)

    source = result.get("source")
    if (
        type(source) is not dict
        or source.get("providerFolder") != context["sourceProviderFolder"]
        or source.get("imapUid") != context["sourceImapUid"]
        or source.get("uidValidity") != context["sourceUidValidity"]
    ):
        return None, _imap_reply_source_error_response(
            {"code": "message_identity_unconfirmed"}
        )

    source_message_id = _normalize_custom_rfc_message_id(source.get("messageId"))
    if source_message_id is None:
        return None, _imap_reply_source_error_response(
            {"code": "imap_reply_source_unthreadable"}
        )

    references = _build_custom_reply_references(
        source.get("references"),
        source.get("inReplyTo"),
        source_message_id,
    )
    return {
        "inReplyTo": source_message_id,
        "references": references,
    }, None


def _prepare_semantic_event_context(
    owned: object,
    *,
    mailbox_id: object,
    provider: str,
) -> dict | None:
    """Capture all potentially blocking semantic authority before provider send."""
    if type(mailbox_id) is not str or provider != "google":
        return None
    try:
        config = load_semantic_runtime_config()
        if config.mode is not SemanticMode.SHADOW or not config.model:
            return None
        secret = resolve_priority_hmac_secret()
        if type(owned) is not dict or owned.get("status") != "ok":
            return None
        authority = priority_authority_from_owned_mailbox(
            owned.get("memberAuthority"),
            owned,
            mailbox_id,
        )
        if authority.provider != provider:
            return None
        return {
            "authority": authority,
            "semanticVersion": config.schema_version,
            "hmacSecret": secret,
        }
    except Exception:
        return None


def _semantic_authority_capture_enabled() -> bool:
    """Return whether this request may need one-pass member authority capture."""
    try:
        config = load_semantic_runtime_config()
        return config.mode is SemanticMode.SHADOW and bool(config.model)
    except Exception:
        return False


def _try_semantic_event_reference(
    prepared_context: object,
    *,
    provider: str,
    provider_conversation_id: object,
    latest_turn_id: object,
    authored_text: object,
) -> str | None:
    """Mint a shadow-only ticket without ever changing send success semantics."""
    if (
        type(prepared_context) is not dict
        or set(prepared_context) != {"authority", "semanticVersion", "hmacSecret"}
        or provider != "google"
        or type(provider_conversation_id) is not str
        or type(latest_turn_id) is not str
    ):
        return None
    try:
        authority = prepared_context["authority"]
        semantic_version = prepared_context["semanticVersion"]
        secret = prepared_context["hmacSecret"]
        if (
            not isinstance(authority, PriorityAuthority)
            or type(semantic_version) is not str
            or type(secret) is not str
        ):
            return None
        now_ms = int(time.time() * 1_000)
        reference, _conversation_id = mint_outgoing_event_reference_for_authority(
            authority,
            provider=provider,
            provider_conversation_id=provider_conversation_id,
            latest_turn_id=latest_turn_id,
            authored_text=authored_text,
            occurred_at=now_ms,
            semantic_version=semantic_version,
            hmac_secret=secret,
            now=now_ms // 1_000,
        )
        return reference
    except Exception:
        return None


def _decode_reply_subject(value: str) -> str | None:
    try:
        fragments = decode_header(value)
    except (HeaderParseError, TypeError, ValueError):
        return None

    decoded_fragments: list[str] = []
    for fragment, charset in fragments:
        if isinstance(fragment, bytes):
            try:
                decoded_fragment = fragment.decode(charset or "ascii", errors="strict")
            except (LookupError, UnicodeDecodeError):
                return None
        else:
            decoded_fragment = fragment
        decoded_fragments.append(decoded_fragment)
    decoded = "".join(decoded_fragments)
    return decoded if len(decoded) <= MAX_SUBJECT_CHARACTERS else None


def _canonical_reply_subject(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    decoded = _decode_reply_subject(value)
    if decoded is None:
        return None
    normalized = decoded.strip()
    if (
        len(normalized) > MAX_SUBJECT_CHARACTERS
        or _has_unsafe_header_chars(normalized)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    normalized = " ".join(normalized.split())
    while _REPLY_SUBJECT_PREFIX_PATTERN.match(normalized):
        normalized = _REPLY_SUBJECT_PREFIX_PATTERN.sub("", normalized, count=1).strip()
    return normalized.casefold()


def _metadata_headers(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    raw_payload = payload.get("payload")
    raw_headers = raw_payload.get("headers") if isinstance(raw_payload, dict) else None
    if not isinstance(raw_headers, list) or len(raw_headers) > MAX_GMAIL_METADATA_HEADERS:
        return None

    selected: dict[str, str] = {}
    total_characters = 0
    requested_names = {name.casefold() for name in GMAIL_REPLY_METADATA_HEADERS}
    for raw_header in raw_headers:
        if not isinstance(raw_header, dict):
            return None
        name = raw_header.get("name")
        value = raw_header.get("value")
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or not name
            or len(name) > MAX_GMAIL_METADATA_HEADER_NAME_CHARACTERS
            or len(value) > MAX_GMAIL_METADATA_HEADER_VALUE_CHARACTERS
        ):
            return None
        total_characters += len(name) + len(value)
        if total_characters > MAX_GMAIL_METADATA_TOTAL_CHARACTERS:
            return None
        normalized_name = name.casefold()
        if normalized_name not in requested_names:
            continue
        if normalized_name in selected:
            return None
        selected[normalized_name] = value
    return selected


def _validate_reply_source(
    payload: object,
    *,
    requested_message_id: str,
    outgoing_subject: object,
) -> tuple[dict | None, dict | None]:
    if (
        not isinstance(payload, dict)
        or payload.get("id") != requested_message_id
        or not _valid_gmail_identifier(payload.get("id"))
        or not _valid_gmail_identifier(payload.get("threadId"))
    ):
        return None, {"code": "gmail_reply_source_invalid"}

    headers = _metadata_headers(payload)
    if headers is None:
        return None, {"code": "gmail_reply_source_invalid"}
    source_message_id = _normalize_rfc_message_id(headers.get("message-id"))
    source_subject = _canonical_reply_subject(headers.get("subject"))
    normalized_outgoing_subject = _canonical_reply_subject(outgoing_subject)
    if source_message_id is None or source_subject is None:
        return None, {"code": "gmail_reply_source_invalid"}
    if normalized_outgoing_subject is None or source_subject != normalized_outgoing_subject:
        return None, {"code": "gmail_reply_subject_mismatch"}

    raw_references = headers.get("references", "")
    raw_in_reply_to = headers.get("in-reply-to", "")
    _parse_rfc_message_id_tokens(raw_in_reply_to)
    references = _build_reply_references(raw_references, source_message_id)
    return {
        "threadId": payload["threadId"],
        "inReplyTo": source_message_id,
        "references": references,
    }, None


def _gmail_api_get_reply_source(
    access_token: str,
    source_message_id: str,
) -> tuple[dict | None, dict | None]:
    query = urlencode(
        [
            ("format", "metadata"),
            *(
                ("metadataHeaders", header_name)
                for header_name in GMAIL_REPLY_METADATA_HEADERS
            ),
        ]
    )
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/{quote(source_message_id, safe='')}?{query}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
            if body is None:
                return None, {"code": "gmail_response_too_large"}
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                return None, {"code": "gmail_response_invalid"}
            if not isinstance(payload, dict):
                return None, {"code": "gmail_response_invalid"}
            return payload, None
    except HTTPError as error:
        if error.code == 404:
            return None, {"code": "gmail_reply_source_not_found"}
        return None, {
            "code": gmail_http_error_code(
                error.code,
                "gmail_reply_source_fetch_failed",
            )
        }
    except IncompleteRead:
        return None, {"code": "gmail_response_invalid"}
    except (OSError, URLError, TimeoutError):
        return None, {"code": "gmail_unavailable"}


def _gmail_api_send(
    access_token: str,
    message: EmailMessage,
    *,
    thread_id: str | None = None,
) -> tuple[dict | None, dict | None]:
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    gmail_payload = {"raw": encoded_message}
    if thread_id is not None:
        gmail_payload["threadId"] = thread_id
    request = Request(
        f"{GMAIL_API_BASE_URL}/messages/send",
        data=json.dumps(gmail_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    response_received = False
    try:
        with urlopen(request, timeout=30) as response:
            response_received = True
            body = read_bounded_response(response, MAX_GMAIL_RESPONSE_BYTES)
            if body is None:
                return None, None
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                return None, None
            if not isinstance(payload, dict):
                return None, None
            return payload, None
    except HTTPError as error:
        return None, {"code": gmail_http_error_code(error.code, "gmail_send_failed")}
    except (IncompleteRead, OSError, URLError, TimeoutError):
        if response_received:
            return None, None
        return None, {"code": "gmail_unavailable"}


def _send_with_gmail_oauth(
    context: dict,
    message: EmailMessage,
    *,
    thread_id: str | None = None,
) -> tuple[dict | None, dict | None, dict | None]:
    send_payload, send_error = _gmail_api_send(
        context["access_token"],
        message,
        thread_id=thread_id,
    )
    if send_error and send_error.get("code") == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed["status"] != "ok":
            return None, None, refreshed
        context = refreshed["context"]
        send_payload, send_error = _gmail_api_send(
            context["access_token"],
            message,
            thread_id=thread_id,
        )
    return send_payload, send_error, None


def _validated_gmail_send_identity(payload: object) -> dict | None:
    if (
        not isinstance(payload, dict)
        or not _valid_gmail_identifier(payload.get("id"))
        or not _valid_gmail_identifier(payload.get("threadId"))
    ):
        return None

    identity = {
        "providerMessageId": payload["id"],
        "providerThreadId": payload["threadId"],
    }
    raw_label_ids = payload.get("labelIds")
    if (
        isinstance(raw_label_ids, list)
        and len(raw_label_ids) <= MAX_GMAIL_SEND_LABELS
        and all(_valid_gmail_identifier(label_id) for label_id in raw_label_ids)
        and len(raw_label_ids) == len(set(raw_label_ids))
    ):
        identity["labelIds"] = list(raw_label_ids)
    return identity


def _reply_source_error_response(error: dict) -> tuple[int, dict]:
    code = error.get("code")
    mapping = {
        "gmail_reply_source_not_found": (
            404,
            "gmail_reply_source_not_found",
            "The Gmail message being replied to was not found.",
        ),
        "gmail_reply_source_invalid": (
            502,
            "gmail_reply_source_invalid",
            "Gmail returned invalid reply-thread information.",
        ),
        "gmail_reply_subject_mismatch": (
            400,
            "gmail_reply_subject_mismatch",
            "The reply subject does not match the Gmail source message.",
        ),
        "gmail_token_invalid": (
            401,
            "reconnect_required",
            "Reconnect this Gmail inbox to continue.",
        ),
        "gmail_permission_denied": (
            403,
            "gmail_reply_source_permission_denied",
            "Gmail did not permit the reply source lookup.",
        ),
        "gmail_rate_limited": (
            502,
            "gmail_reply_source_rate_limited",
            "Gmail is temporarily rate limited.",
        ),
        "gmail_unavailable": (
            502,
            "gmail_reply_source_unavailable",
            "Gmail is temporarily unavailable.",
        ),
        "gmail_response_too_large": (
            502,
            "gmail_reply_source_invalid",
            "Gmail returned invalid reply-thread information.",
        ),
        "gmail_response_invalid": (
            502,
            "gmail_reply_source_invalid",
            "Gmail returned invalid reply-thread information.",
        ),
    }
    status, response_code, message = mapping.get(
        code,
        (
            502,
            "gmail_reply_source_fetch_failed",
            "The Gmail reply source could not be loaded.",
        ),
    )
    return status, error_payload(response_code, message)


def _imap_reply_source_error_response(error: dict) -> tuple[int, dict]:
    code = error.get("code")
    if code in {"invalid_folder", "invalid_imap_uid", "invalid_uid_validity"}:
        return 400, error_payload(
            "invalid_imap_reply_context",
            "IMAP reply context must identify exactly one valid source message.",
        )
    if code in {"uid_validity_changed", "message_not_found", "folder_unavailable"}:
        return 409, error_payload(
            "imap_reply_source_stale",
            "The IMAP message being replied to is no longer available at that identity.",
        )
    if code == "imap_reply_source_unthreadable":
        return 422, error_payload(
            "imap_reply_source_unthreadable",
            "The IMAP message being replied to has no unambiguous Message-ID.",
        )
    return 503, error_payload(
        "imap_reply_source_unavailable",
        "The IMAP reply source is temporarily unavailable.",
    )


def _custom_imap_connection_config(mailbox: object) -> tuple[str, int, bool, str, str]:
    if type(mailbox) is not dict or type(mailbox.get("imap")) is not dict:
        raise ValueError("Stored IMAP configuration is invalid.")
    config = mailbox["imap"]
    host = config.get("host")
    raw_port = config.get("port")
    if type(raw_port) is int:
        port = raw_port
    elif (
        type(raw_port) is str
        and len(raw_port) <= 5
        and _CANONICAL_IMAP_NUMBER_PATTERN.fullmatch(raw_port) is not None
    ):
        port = int(raw_port)
    else:
        raise ValueError("Stored IMAP configuration is invalid.")
    use_ssl = config.get("ssl")
    username = config.get("username")
    password = config.get("password")
    if (
        type(host) is not str
        or not host
        or host != host.strip()
        or _has_unsafe_header_chars(host)
        or any(character.isspace() for character in host)
        or not 1 <= port <= 65535
        or use_ssl is not True
        or type(username) is not str
        or not _is_safe_auth_value(username)
        or type(password) is not str
        or not _is_safe_auth_value(password)
    ):
        raise ValueError("Stored IMAP configuration is invalid.")
    return host, port, use_ssl, username, password


def _safe_close_custom_imap(connection: object) -> None:
    try:
        connection.logout()
    except Exception:
        pass


def _open_custom_imap_connection(mailbox: object):
    host, port, _use_ssl, username, password = _custom_imap_connection_config(
        mailbox
    )
    try:
        return connect_mailbox_with_settings(
            host,
            port,
            username,
            password,
            True,
            timeout=30,
        )
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error as error:
        raise _CustomImapAuthenticationError from error


def _add_custom_message_identity_headers(message: EmailMessage) -> None:
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid()


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
    if (security, port) not in {("ssl", 465), ("starttls", 587)}:
        raise ValueError("SMTP security and port combination is invalid.")

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
            "replyContext", "imapReplyContext",
        }
        field_error = reject_unknown_fields(payload, allowed_fields)
        if field_error:
            send_json(
                self,
                400,
                error_payload("forbidden_connection_fields", "Connection and identity details are not accepted."),
            )
            return
        reply_context, reply_context_error = _validate_reply_context(payload)
        if reply_context_error:
            send_json(self, 400, reply_context_error)
            return
        imap_reply_context, imap_reply_context_error = (
            _validate_imap_reply_context(payload)
        )
        if imap_reply_context_error:
            send_json(self, 400, imap_reply_context_error)
            return
        connection_field_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"replyContext", "imapReplyContext"}
        }
        if find_forbidden_custom_request_fields(connection_field_payload):
            send_json(
                self,
                400,
                error_payload(
                    "forbidden_connection_fields",
                    "Connection and identity details are not accepted.",
                ),
            )
            return

        if reply_context is not None and _semantic_authority_capture_enabled():
            owned = resolve_owned_mailbox(
                self.headers,
                payload.get("mailboxId"),
                include_member_authority=True,
            )
        else:
            owned = resolve_owned_mailbox(
                self.headers,
                payload.get("mailboxId"),
            )
        if owned["status"] != "ok":
            send_json(self, owned["status_code"], owned["error"])
            return
        provider = owned["inbox"].get("provider")

        if provider == "google":
            if imap_reply_context is not None:
                send_json(
                    self,
                    400,
                    error_payload(
                        "invalid_imap_reply_context",
                        "IMAP reply context cannot be used with this mailbox.",
                    ),
                )
                return
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

            reply_source = None
            if reply_context is not None:
                source_message_id = reply_context["sourceProviderMessageId"]
                source_payload, source_error = _gmail_api_get_reply_source(
                    context["access_token"],
                    source_message_id,
                )
                if (
                    source_error
                    and source_error.get("code") == "gmail_token_invalid"
                    and not context["refresh_attempted"]
                ):
                    refreshed = refresh_gmail_context(context)
                    if refreshed["status"] != "ok":
                        send_json(
                            self,
                            refreshed["status_code"],
                            refreshed["error"],
                        )
                        return
                    context = refreshed["context"]
                    source_payload, source_error = _gmail_api_get_reply_source(
                        context["access_token"],
                        source_message_id,
                    )
                if source_error:
                    status, response = _reply_source_error_response(source_error)
                    send_json(self, status, response)
                    return
                reply_source, source_validation_error = _validate_reply_source(
                    source_payload,
                    requested_message_id=source_message_id,
                    outgoing_subject=str(message.get("Subject", "")),
                )
                if source_validation_error:
                    status, response = _reply_source_error_response(
                        source_validation_error
                    )
                    send_json(self, status, response)
                    return
                message["In-Reply-To"] = reply_source["inReplyTo"]
                message["References"] = reply_source["references"]

            # The optional context is captured before transport. This work is
            # environment/config parsing and construction from the authority
            # already returned above; it performs no network or store I/O.
            semantic_event_context = (
                _prepare_semantic_event_context(
                    owned,
                    mailbox_id=payload.get("mailboxId"),
                    provider="google",
                )
                if reply_source is not None
                else None
            )

            send_payload, send_error, refresh_failure = _send_with_gmail_oauth(
                context,
                message,
                thread_id=(reply_source or {}).get("threadId"),
            )
            if refresh_failure:
                send_json(self, refresh_failure["status_code"], refresh_failure["error"])
                return
            if send_error:
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
            send_identity = _validated_gmail_send_identity(send_payload)
            if send_identity is None:
                unconfirmed_response = {
                    "ok": True,
                    "providerIdentityConfirmed": False,
                    "warning": {
                        "code": "gmail_send_identity_unconfirmed",
                        "message": (
                            "The message was sent, but Gmail did not return "
                            "a valid provider identity."
                        ),
                    },
                }
                if reply_source is not None:
                    unconfirmed_response["threadContinuityConfirmed"] = False
                send_json(self, 200, unconfirmed_response)
                return

            response = {"ok": True, **send_identity}
            if reply_source is not None:
                continuity_confirmed = (
                    send_identity["providerThreadId"] == reply_source["threadId"]
                )
                response["threadContinuityConfirmed"] = continuity_confirmed
                if not continuity_confirmed:
                    response["warning"] = {
                        "code": "gmail_thread_continuity_unconfirmed",
                        "message": (
                            "The message was sent, but Gmail returned a different "
                            "conversation identity."
                        ),
                    }
                else:
                    semantic_event_ref = _try_semantic_event_reference(
                        semantic_event_context,
                        provider="google",
                        provider_conversation_id=send_identity["providerThreadId"],
                        latest_turn_id=send_identity["providerMessageId"],
                        authored_text=payload.get("bodyText"),
                    )
                    if semantic_event_ref is not None:
                        response["semanticEventRef"] = semantic_event_ref
            send_json(self, 200, response)
            return

        if reply_context is not None:
            send_json(
                self,
                400,
                error_payload(
                    "invalid_reply_context",
                    "Gmail reply context cannot be used with this mailbox.",
                ),
            )
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

        _add_custom_message_identity_headers(message)

        custom_reply_source = None
        if imap_reply_context is not None:
            imap_connection = None
            try:
                imap_connection = _open_custom_imap_connection(mailbox)
                source_result = read_imap_reply_source(
                    imap_connection,
                    folder=imap_reply_context["sourceProviderFolder"],
                    uid=imap_reply_context["sourceImapUid"],
                    expected_uid_validity=imap_reply_context[
                        "sourceUidValidity"
                    ],
                )
            except _CustomImapAuthenticationError:
                send_json(
                    self,
                    401,
                    error_payload(
                        "reconnect_required",
                        "Reconnect this IMAP inbox to continue.",
                    ),
                )
                return
            except Exception:
                status, response = _imap_reply_source_error_response({})
                send_json(self, status, response)
                return
            finally:
                if imap_connection is not None:
                    _safe_close_custom_imap(imap_connection)

            custom_reply_source, source_response = _validate_custom_reply_source(
                source_result,
                imap_reply_context,
            )
            if source_response is not None:
                status, response = source_response
                send_json(self, status, response)
                return
            message["In-Reply-To"] = custom_reply_source["inReplyTo"]
            message["References"] = custom_reply_source["references"]

        try:
            send_public_smtp_message(
                smtp_host,
                smtp_port,
                smtp_security,
                username,
                password,
                message,
                recipients,
                timeout=30,
            )
        except SmtpConnectionError as error:
            if error.code == "smtp_authentication_failed":
                send_json(self, 401, error_payload("invalid_credentials", "Stored SMTP credentials were rejected."))
            elif error.code == "smtp_send_failed":
                send_json(self, 502, error_payload("send_failed", "SMTP could not send this message."))
            else:
                send_json(self, 502, error_payload("send_failed", "SMTP connection could not send this message."))
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
