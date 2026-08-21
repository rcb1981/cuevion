"""Server-only tenant, mailbox, and provider authority for Priority semantics."""

from __future__ import annotations

import base64
import json
import quopri
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.utils import getaddresses
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from api.inboxes.authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES,
    gmail_http_error_code,
    refresh_gmail_context,
    resolve_gmail_context,
)
from api.inboxes.authenticated_imap import resolve_authenticated_imap_mailbox
from api.inboxes.gmail_thread_parser import (
    GmailThreadParseError,
    parse_gmail_thread,
)
from api.inboxes.imap_snapshot import (
    _is_absent_uid_fetch_response,
    _is_ok_status,
    _response_parts,
    read_imap_latest_thread_identity,
    read_imap_reply_source,
)
from api.user_config_store import (
    resolve_authenticated_member_authority,
    resolve_owned_managed_inbox_record,
)
from imap_connect_preview import (
    build_bounded_thread_identity,
    connect_mailbox_with_settings,
    extract_message_thread_metadata,
    normalize_message_id_token,
)

from .event_reference import (
    OutgoingEventClaims,
    issue_outgoing_event_reference,
)


GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
PROVIDER_TIMEOUT_SECONDS = 6
MAX_IDENTIFIER_CHARACTERS = 1_024
_IMAP_NUMBER_RE = re.compile(r"[1-9][0-9]*", re.ASCII)
_IMAP_INTERNALDATE_FETCH_RE = re.compile(
    r'\A([1-9][0-9]*) \(UID ([1-9][0-9]*) INTERNALDATE "'
    r'((?: [1-9]|[0-3][0-9])-[A-Z][a-z]{2}-[0-9]{4} '
    r'[0-2][0-9]:[0-5][0-9]:[0-5][0-9] [+-][0-2][0-9][0-5][0-9])" '
    r'BODY\[\] \{(0|[1-9][0-9]*)\}(\))?\Z',
    re.ASCII,
)
_MAX_IMAP_NUMBER = 4_294_967_295
_MAX_IMAP_FETCH_METADATA_BYTES = 4_096
_MAX_IMAP_MESSAGE_BYTES = 25 * 1_024 * 1_024
_MAX_SEMANTIC_MIME_PART_BYTES = 256 * 1_024
_MAX_SEMANTIC_MIME_TOTAL_BYTES = 512 * 1_024
_MAX_SEMANTIC_MIME_PARTS = 128
_MAX_SEMANTIC_MIME_DEPTH = 32
_ENCODE_URI_COMPONENT_SAFE = "-_.!~*'()"
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


class SemanticAuthorityError(Exception):
    """A fixed, value-free authority/provider rejection."""

    __slots__ = ("code", "status")

    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status if type(status) is int and 400 <= status <= 599 else 500
        Exception.__init__(self, code)


@dataclass(frozen=True, slots=True)
class PriorityAuthority:
    workspace_id: str
    user_id: str
    member_email: str
    mailbox_id: str
    provider: str
    mailbox_email: str
    owned_emails: frozenset[str]
    user_record: dict[str, Any]
    inbox_record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuthorizedSemanticSource:
    authority: PriorityAuthority
    conversation_id: str
    provider_conversation_id: str
    latest_turn_id: str
    occurred_at: int
    turns: tuple[dict[str, Any], ...]
    revalidation_locator: dict[str, str] | None


def _valid_identifier(value: object, maximum: int = MAX_IDENTIFIER_CHARACTERS) -> bool:
    return (
        type(value) is str
        and value == value.strip()
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _same_email(left: object, right: object) -> bool:
    return (
        type(left) is str
        and type(right) is str
        and bool(left.strip())
        and left.strip().casefold() == right.strip().casefold()
    )


def _single_header_address(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or any(ord(character) < 32 and character not in "\t" for character in value)
        or "\r" in value
        or "\n" in value
    ):
        return None
    try:
        parsed = getaddresses([value])
    except Exception:
        return None
    if len(parsed) != 1:
        return None
    address = parsed[0][1].strip().casefold()
    if (
        not address
        or len(address) > 320
        or re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+", address) is None
    ):
        return None
    return address


def _encode_component(value: str) -> str:
    return quote(value.strip() or "missing", safe=_ENCODE_URI_COMPONENT_SAFE)


def canonical_conversation_id(mailbox_id: str, provider_thread_id: str) -> str:
    return f"thread:{_encode_component(mailbox_id)}|{provider_thread_id}"


def gmail_thread_id(mailbox_id: str, provider_thread_id: str) -> str:
    return ":".join(
        (
            "gmail",
            _encode_component(mailbox_id),
            _encode_component(provider_thread_id),
        )
    )


def custom_imap_thread_id(mailbox_id: str, root_message_id: str) -> str:
    return build_bounded_thread_identity("imap:rfc", mailbox_id, root_message_id)


def priority_authority_from_owned_mailbox(
    member: object,
    owned: object,
    mailbox_id: object,
) -> PriorityAuthority:
    """Build authority from one authenticated mailbox read without further I/O."""
    if not _valid_identifier(mailbox_id, 256):
        raise SemanticAuthorityError("invalid_mailbox_id", 400)
    if type(owned) is not dict:
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)

    member_email = getattr(member, "email", None)
    workspace_id = getattr(member, "workspace_id", None)
    user_id = getattr(member, "user_id", None)
    if (
        not _valid_identifier(member_email, 320)
        or not _valid_identifier(workspace_id)
        or not _valid_identifier(user_id)
    ):
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)

    user = owned.get("user")
    inbox = owned.get("inbox")
    config = owned.get("config")
    if type(user) is not dict or type(inbox) is not dict:
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)
    if not _same_email(user.get("email"), member_email):
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)
    if inbox.get("id") != mailbox_id:
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)
    provider = inbox.get("provider")
    if provider not in {"google", "custom_imap"}:
        raise SemanticAuthorityError("unsupported_provider", 400)
    if inbox.get("connected") is not True or inbox.get("connectionStatus") != "connected":
        raise SemanticAuthorityError("mailbox_not_ready", 409)
    mailbox_email = inbox.get("email")
    if type(mailbox_email) is not str or not mailbox_email.strip():
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)

    owned_emails = {member_email.strip().casefold(), mailbox_email.strip().casefold()}
    if type(config) is dict and type(config.get("managedInboxes")) is list:
        for managed_inbox in config["managedInboxes"]:
            if type(managed_inbox) is not dict:
                continue
            managed_email = managed_inbox.get("email")
            if type(managed_email) is str and managed_email.strip():
                owned_emails.add(managed_email.strip().casefold())

    return PriorityAuthority(
        workspace_id=workspace_id,
        user_id=user_id,
        member_email=member_email,
        mailbox_id=mailbox_id,
        provider=provider,
        mailbox_email=mailbox_email.strip().casefold(),
        owned_emails=frozenset(owned_emails),
        user_record=dict(user),
        inbox_record=dict(inbox),
    )


def resolve_priority_authority(headers, mailbox_id: object) -> PriorityAuthority:
    if not _valid_identifier(mailbox_id, 256):
        raise SemanticAuthorityError("invalid_mailbox_id", 400)

    member, member_error = resolve_authenticated_member_authority(headers)
    if member is None:
        code = (member_error or {}).get("code")
        if code == "session_auth_unavailable":
            raise SemanticAuthorityError("session_auth_unavailable", 503)
        raise SemanticAuthorityError("unauthorized", 401)

    owned = resolve_owned_managed_inbox_record(headers, mailbox_id)
    if owned.get("status") == "unauthorized":
        raise SemanticAuthorityError("unauthorized", 401)
    if owned.get("status") == "not_found":
        raise SemanticAuthorityError("mailbox_not_found", 404)
    if owned.get("status") != "ok":
        raise SemanticAuthorityError("mailbox_authority_unavailable", 503)

    return priority_authority_from_owned_mailbox(
        member,
        owned,
        mailbox_id,
    )


def verify_claim_scope(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
    *,
    semantic_version: str,
) -> None:
    if (
        claims.workspace_id != authority.workspace_id
        or claims.user_id != authority.user_id
        or claims.mailbox_id != authority.mailbox_id
        or claims.provider != authority.provider
        or claims.semantic_version != semantic_version
    ):
        raise SemanticAuthorityError("event_scope_mismatch", 403)


def mint_outgoing_event_reference(
    headers,
    *,
    mailbox_id: str,
    provider: str,
    provider_conversation_id: str,
    latest_turn_id: str,
    authored_text: object,
    occurred_at: int,
    semantic_version: str,
    hmac_secret: str,
    now: int | None = None,
) -> tuple[str, str]:
    authority = resolve_priority_authority(headers, mailbox_id)
    return mint_outgoing_event_reference_for_authority(
        authority,
        provider=provider,
        provider_conversation_id=provider_conversation_id,
        latest_turn_id=latest_turn_id,
        authored_text=authored_text,
        occurred_at=occurred_at,
        semantic_version=semantic_version,
        hmac_secret=hmac_secret,
        now=now,
    )


def mint_outgoing_event_reference_for_authority(
    authority: PriorityAuthority,
    *,
    provider: str,
    provider_conversation_id: str,
    latest_turn_id: str,
    authored_text: object,
    occurred_at: int,
    semantic_version: str,
    hmac_secret: str,
    now: int | None = None,
) -> tuple[str, str]:
    """Mint from authority captured before provider send; performs no I/O."""
    if not isinstance(authority, PriorityAuthority):
        raise SemanticAuthorityError("event_scope_mismatch", 403)
    if authority.provider != provider:
        raise SemanticAuthorityError("event_scope_mismatch", 403)
    if provider != "google":
        # Generic SMTP does not expose a provider-authoritative sent identity.
        # It therefore cannot safely activate an outgoing semantic event.
        raise SemanticAuthorityError("unsupported_provider", 400)
    if not _valid_identifier(provider_conversation_id) or not _valid_identifier(
        latest_turn_id
    ):
        raise SemanticAuthorityError("provider_identity_unconfirmed", 409)
    thread_id = gmail_thread_id(authority.mailbox_id, provider_conversation_id)
    conversation_id = canonical_conversation_id(authority.mailbox_id, thread_id)
    reference = issue_outgoing_event_reference(
        secret=hmac_secret,
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        mailbox_id=authority.mailbox_id,
        provider=provider,
        conversation_id=conversation_id,
        provider_conversation_id=provider_conversation_id,
        latest_turn_id=latest_turn_id,
        authored_text=authored_text,
        occurred_at=occurred_at,
        semantic_version=semantic_version,
        now=now,
    )
    return reference, conversation_id


def _gmail_thread_request(access_token: str, thread_id: str) -> tuple[object | None, str | None]:
    request = Request(
        f"{GMAIL_API_BASE_URL}/threads/{quote(thread_id, safe='')}?format=full",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                if not content_length.isascii() or not content_length.isdigit():
                    return None, "gmail_response_invalid"
                if int(content_length) > MAX_GMAIL_RESPONSE_BYTES:
                    return None, "gmail_thread_too_large"
            body = response.read(MAX_GMAIL_RESPONSE_BYTES + 1)
            if len(body) > MAX_GMAIL_RESPONSE_BYTES:
                return None, "gmail_thread_too_large"
            try:
                return json.loads(body.decode("utf-8")), None
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                return None, "gmail_response_invalid"
    except HTTPError as error:
        if error.code == 404:
            return None, "incoming_message_not_found"
        return None, gmail_http_error_code(error.code, "gmail_unavailable")
    except (IncompleteRead, OSError, URLError, TimeoutError):
        return None, "gmail_unavailable"


def _external_gmail_turn(
    message: dict,
    authority: PriorityAuthority,
) -> bool | None:
    labels = message.get("labelIds")
    if type(labels) is list and "SENT" in labels:
        return False
    sender_email = _single_header_address(message.get("from"))
    if sender_email is None:
        return None
    return sender_email not in authority.owned_emails


def _load_gmail_thread_messages(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
) -> list[dict]:
    owned = {"user": authority.user_record, "inbox": authority.inbox_record}
    resolution = resolve_gmail_context(owned)
    if resolution.get("status") != "ok":
        status_code = int(resolution.get("status_code") or 503)
        raise SemanticAuthorityError("gmail_authority_unavailable", status_code)
    context = resolution["context"]
    payload, error = _gmail_thread_request(
        context["access_token"], claims.provider_conversation_id
    )
    if error == "gmail_token_invalid" and not context["refresh_attempted"]:
        refreshed = refresh_gmail_context(context)
        if refreshed.get("status") != "ok":
            raise SemanticAuthorityError("gmail_authority_unavailable", 503)
        context = refreshed["context"]
        payload, error = _gmail_thread_request(
            context["access_token"], claims.provider_conversation_id
        )
    if error:
        status = 404 if error == "incoming_message_not_found" else 503
        raise SemanticAuthorityError(error, status)
    try:
        messages = parse_gmail_thread(payload, claims.provider_conversation_id)
    except (GmailThreadParseError, OverflowError):
        raise SemanticAuthorityError("gmail_response_invalid", 503) from None
    if not messages:
        raise SemanticAuthorityError("incoming_message_not_found", 404)
    return messages


def _gmail_event_and_target_indexes(
    messages: list[dict],
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
    *,
    expected_message_id: str,
) -> tuple[int, int]:
    event_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("providerMessageId") == claims.latest_turn_id
    ]
    target_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("providerMessageId") == expected_message_id
    ]
    if len(event_indexes) != 1 or len(target_indexes) != 1:
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
    event_index = event_indexes[0]
    target_index = target_indexes[0]
    event = messages[event_index]
    labels = event.get("labelIds")
    if (
        type(labels) is not list
        or "SENT" not in labels
        or _external_gmail_turn(event, authority) is not False
    ):
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
    if target_index < event_index or (
        target_index == event_index and expected_message_id != claims.latest_turn_id
    ):
        raise SemanticAuthorityError("incoming_message_stale", 409)
    if expected_message_id != claims.latest_turn_id and _external_gmail_turn(
        messages[target_index], authority
    ) is not True:
        raise SemanticAuthorityError("incoming_message_not_external", 409)
    return event_index, target_index


def prove_authorized_gmail_latest(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
    *,
    expected_message_id: str,
) -> None:
    if authority.provider != "google" or claims.provider != "google":
        raise SemanticAuthorityError("provider_mismatch", 400)
    messages = _load_gmail_thread_messages(authority, claims)
    _event_index, target_index = _gmail_event_and_target_indexes(
        messages,
        authority,
        claims,
        expected_message_id=expected_message_id,
    )
    if target_index != len(messages) - 1:
        raise SemanticAuthorityError("incoming_message_stale", 409)
    if messages[-1].get("providerMessageId") != expected_message_id:
        raise SemanticAuthorityError("incoming_message_stale", 409)
    thread_id = gmail_thread_id(authority.mailbox_id, claims.provider_conversation_id)
    if canonical_conversation_id(authority.mailbox_id, thread_id) != claims.conversation_id:
        raise SemanticAuthorityError("conversation_mismatch", 409)


def load_authorized_gmail_incoming(
    authority: PriorityAuthority,
    claims: OutgoingEventClaims,
    *,
    provider_message_id: object,
) -> AuthorizedSemanticSource:
    if authority.provider != "google" or claims.provider != "google":
        raise SemanticAuthorityError("provider_mismatch", 400)
    if not _valid_identifier(provider_message_id, 256):
        raise SemanticAuthorityError("invalid_incoming_locator", 400)

    messages = _load_gmail_thread_messages(authority, claims)
    _event_index, target_index = _gmail_event_and_target_indexes(
        messages,
        authority,
        claims,
        expected_message_id=provider_message_id,
    )
    target = next(
        (
            message
            for message in messages
            if message.get("providerMessageId") == provider_message_id
        ),
        None,
    )
    if target is None:
        raise SemanticAuthorityError("incoming_message_not_found", 404)
    if target_index != len(messages) - 1 or messages[-1].get(
        "providerMessageId"
    ) != provider_message_id:
        raise SemanticAuthorityError("incoming_message_stale", 409)
    try:
        occurred_at = int(target.get("internalDate"))
    except (TypeError, ValueError):
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409) from None
    if _external_gmail_turn(target, authority) is not True:
        raise SemanticAuthorityError("incoming_message_not_external", 409)

    thread_id = gmail_thread_id(authority.mailbox_id, claims.provider_conversation_id)
    conversation_id = canonical_conversation_id(authority.mailbox_id, thread_id)
    if conversation_id != claims.conversation_id:
        raise SemanticAuthorityError("conversation_mismatch", 409)

    turns: list[dict[str, Any]] = []
    for message in messages[-3:]:
        external = _external_gmail_turn(message, authority)
        if external is None:
            continue
        body_html = message.get("bodyHtml")
        body_text = (
            body_html
            if type(body_html) is str and body_html.strip()
            else message.get("bodyText")
        )
        if type(body_text) is not str or not body_text.strip():
            continue
        turns.append(
            {
                "turnId": message["providerMessageId"],
                "speaker": "external" if external else "user",
                "direction": "incoming" if external else "outgoing",
                "text": body_text,
                "timestamp": message.get("createdAt"),
            }
        )
    if not turns or turns[-1]["turnId"] != provider_message_id:
        raise SemanticAuthorityError("incoming_message_text_unavailable", 422)
    return AuthorizedSemanticSource(
        authority=authority,
        conversation_id=conversation_id,
        provider_conversation_id=claims.provider_conversation_id,
        latest_turn_id=provider_message_id,
        occurred_at=occurred_at,
        turns=tuple(turns),
        revalidation_locator={
            "provider": "google",
            "providerMessageId": provider_message_id,
        },
    )


def _valid_imap_number(value: object) -> bool:
    if type(value) is not str or _IMAP_NUMBER_RE.fullmatch(value) is None:
        return False
    maximum = str(_MAX_IMAP_NUMBER)
    return len(value) < len(maximum) or (
        len(value) == len(maximum) and value <= maximum
    )


def _valid_imap_folder(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 16_384
    except UnicodeEncodeError:
        return False


def _safe_close_imap(connection: object) -> None:
    try:
        connection.logout()
    except Exception:
        pass


def _open_authenticated_imap(mailbox: dict):
    config = mailbox.get("imap")
    if type(config) is not dict:
        raise SemanticAuthorityError("imap_authority_unavailable", 503)
    host = config.get("host")
    port = config.get("port")
    username = config.get("username")
    password = config.get("password")
    if (
        type(host) is not str
        or not host
        or any(character.isspace() for character in host)
        or type(port) is not int
        or type(port) is bool
        or not 1 <= port <= 65_535
        or config.get("ssl") is not True
        or type(username) is not str
        or not username
        or type(password) is not str
        or not password
    ):
        raise SemanticAuthorityError("imap_authority_unavailable", 503)
    try:
        return connect_mailbox_with_settings(
            host,
            port,
            username,
            password,
            True,
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
    except Exception:
        raise SemanticAuthorityError("imap_unavailable", 503) from None


def _root_from_imap_source(source: dict) -> str | None:
    """Derive an existing-conversation root without target-self fallback."""
    references = source.get("references")
    candidates = list(references) if type(references) is list else []
    if not candidates:
        candidates.append(source.get("inReplyTo"))
    for candidate in candidates:
        normalized = normalize_message_id_token(candidate)
        if normalized is not None:
            return normalized
    return None


def _parse_imap_internaldate(value: str) -> tuple[int, str] | None:
    try:
        date_part, time_part, offset_part = value.split(" ")[-3:]
        day_text, month_text, year_text = date_part.split("-")
        hour_text, minute_text, second_text = time_part.split(":")
        offset_sign = 1 if offset_part[0] == "+" else -1
        offset_hours = int(offset_part[1:3])
        offset_minutes = int(offset_part[3:5])
        if offset_hours > 23 or offset_minutes > 59:
            return None
        parsed = datetime(
            int(year_text),
            _MONTHS[month_text],
            int(day_text.strip()),
            int(hour_text),
            int(minute_text),
            int(second_text),
            tzinfo=timezone(
                offset_sign * timedelta(hours=offset_hours, minutes=offset_minutes)
            ),
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return (
        int(parsed.timestamp() * 1_000),
        parsed.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _parse_imap_semantic_fetch(
    response: object,
    *,
    expected_uid: str,
) -> tuple[bytes, int, str] | None:
    parts = _response_parts(response)
    if parts is None or not _is_ok_status(parts[0]):
        return None
    values = parts[1]
    if type(values) not in (list, tuple) or len(values) not in (1, 2):
        return None
    literal = values[0]
    if type(literal) is not tuple or len(literal) != 2:
        return None
    raw_metadata, raw_message = literal
    if (
        type(raw_metadata) is not bytes
        or len(raw_metadata) > _MAX_IMAP_FETCH_METADATA_BYTES
        or type(raw_message) is not bytes
        or len(raw_message) > _MAX_IMAP_MESSAGE_BYTES
    ):
        return None
    try:
        metadata = raw_metadata.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    match = _IMAP_INTERNALDATE_FETCH_RE.fullmatch(metadata)
    if match is None:
        return None
    sequence_number, fetched_uid, internaldate, literal_size_text, inline_close = (
        match.groups()
    )
    if (
        not _valid_imap_number(sequence_number)
        or fetched_uid != expected_uid
        or not _valid_imap_number(fetched_uid)
        or int(literal_size_text) != len(raw_message)
    ):
        return None
    if len(values) == 1:
        if inline_close != ")":
            return None
    elif inline_close is not None or values[1] not in (b")", ")"):
        return None
    parsed_internaldate = _parse_imap_internaldate(internaldate)
    if parsed_internaldate is None:
        return None
    occurred_at, timestamp = parsed_internaldate
    return raw_message, occurred_at, timestamp


def _raw_imap_thread_identity(
    message: object,
    *,
    imap_uid: str,
) -> tuple[str, str] | None:
    try:
        metadata = extract_message_thread_metadata(
            message,
            imap_uid,
            f"imap-uid-{imap_uid}",
        )
    except Exception:
        return None
    if (
        type(metadata) is not dict
        or metadata.get("message_id_ambiguous") is not False
    ):
        return None
    message_id = normalize_message_id_token(metadata.get("message_id"))
    references = metadata.get("references")
    candidates = list(references) if type(references) is list else []
    if not candidates:
        candidates.append(metadata.get("in_reply_to"))
    root = next(
        (
            normalized
            for candidate in candidates
            if (normalized := normalize_message_id_token(candidate)) is not None
        ),
        None,
    )
    if message_id is None or root is None:
        return None
    return message_id, root


def _strict_mime_transfer_decode(
    encoded_payload: object,
    transfer_encoding: str,
) -> bytes | None:
    if type(encoded_payload) is str:
        try:
            raw = encoded_payload.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            return None
    elif type(encoded_payload) is bytes:
        raw = encoded_payload
    else:
        return None

    if transfer_encoding in {"", "7bit"}:
        return raw if all(byte < 128 for byte in raw) else None

    if transfer_encoding == "base64":
        compact = re.sub(br"[ \t\r\n]", b"", raw)
        if len(compact) % 4 != 0:
            return None
        try:
            decoded = base64.b64decode(compact, validate=True)
        except Exception:
            return None
        # Reject non-canonical pad bits and permissive alternate encodings.
        return decoded if base64.b64encode(decoded) == compact else None

    if transfer_encoding == "quoted-printable":
        index = 0
        hexadecimal = b"0123456789abcdefABCDEF"
        while index < len(raw):
            byte = raw[index]
            if byte == 61:  # '='
                if raw[index : index + 3] == b"=\r\n":
                    index += 3
                    continue
                if raw[index : index + 2] == b"=\n":
                    index += 2
                    continue
                if (
                    index + 2 < len(raw)
                    and raw[index + 1] in hexadecimal
                    and raw[index + 2] in hexadecimal
                ):
                    index += 3
                    continue
                return None
            if byte == 13:
                if index + 1 >= len(raw) or raw[index + 1] != 10:
                    return None
                index += 2
                continue
            if byte == 10 or byte == 9 or byte == 32 or 33 <= byte <= 126:
                index += 1
                continue
            return None
        return quopri.decodestring(raw)

    return None


def _valid_mime_8bit_payload(value: bytes) -> bool:
    if b"\x00" in value:
        return False
    lines = value.split(b"\n")
    if any(len(line.removesuffix(b"\r")) > 998 for line in lines):
        return False
    return all(
        (
            byte != 13
            or index + 1 < len(value) and value[index + 1] == 10
        )
        and (
            byte != 10
            or index > 0 and value[index - 1] == 13
        )
        for index, byte in enumerate(value)
    )


def _extract_semantic_mime_body(message: object) -> str | None:
    """Return bounded authored MIME body while excluding attachment subtrees.

    HTML remains intact for the shared semantic normalizer to remove quoted,
    hidden, and signature structure. Any malformed or oversized candidate text
    part fails the whole extraction closed rather than leaking a partial body.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []
    part_count = 0
    decoded_total = 0

    def walk(part: object, *, depth: int, inside_attachment: bool) -> bool:
        nonlocal part_count, decoded_total
        if depth > _MAX_SEMANTIC_MIME_DEPTH:
            return False
        part_count += 1
        if part_count > _MAX_SEMANTIC_MIME_PARTS:
            return False
        try:
            if getattr(part, "defects", ()):
                return False
            for header_name in (
                "Content-Type",
                "Content-Disposition",
                "Content-Transfer-Encoding",
            ):
                header_values = part.get_all(header_name, [])
                if type(header_values) is not list or len(header_values) > 1:
                    return False
            raw_transfer_encoding = part.get("Content-Transfer-Encoding")
            if raw_transfer_encoding is None:
                transfer_encoding = ""
            elif type(raw_transfer_encoding) is str:
                transfer_encoding = raw_transfer_encoding.strip().casefold()
                if (
                    raw_transfer_encoding != raw_transfer_encoding.strip()
                    or not raw_transfer_encoding.isascii()
                    or transfer_encoding
                    not in {"7bit", "8bit", "base64", "quoted-printable"}
                ):
                    return False
            else:
                return False
            raw_disposition = part.get("Content-Disposition")
            disposition = part.get_content_disposition()
            filename = part.get_filename()
            if raw_disposition is not None and disposition not in {
                "attachment",
                "inline",
            }:
                return False
            is_attachment = (
                inside_attachment
                or disposition == "attachment"
                or (type(filename) is str and bool(filename.strip()))
            )
            content_type = part.get_content_type().casefold()
            if content_type.startswith("message/"):
                # Encapsulated messages are forwarded/attached content even
                # when their MIME headers omit disposition and filename.
                return True
            if part.is_multipart():
                children = part.get_payload()
                if type(children) is not list:
                    return False
                return all(
                    walk(
                        child,
                        depth=depth + 1,
                        inside_attachment=is_attachment,
                    )
                    for child in children
                )
            if is_attachment:
                return True
            if content_type not in {"text/plain", "text/html"}:
                return True
            encoded_payload = part.get_payload(decode=False)
            if type(encoded_payload) is str:
                encoded_size = len(
                    encoded_payload.encode("utf-8", errors="strict")
                )
            elif type(encoded_payload) is bytes:
                encoded_size = len(encoded_payload)
            else:
                return False
            if encoded_size > _MAX_SEMANTIC_MIME_PART_BYTES * 2:
                return False
            strict_transfer_required = transfer_encoding in {
                "",
                "7bit",
                "base64",
                "quoted-printable",
            }
            strict_decoded = (
                _strict_mime_transfer_decode(encoded_payload, transfer_encoding)
                if strict_transfer_required
                else None
            )
            if (
                strict_transfer_required and strict_decoded is None
            ):
                return False
            decoded = part.get_payload(decode=True)
            if (
                getattr(part, "defects", ())
                or type(decoded) is not bytes
                or (strict_decoded is not None and decoded != strict_decoded)
                or (
                    transfer_encoding == "8bit"
                    and not _valid_mime_8bit_payload(decoded)
                )
            ):
                return False
            if len(decoded) > _MAX_SEMANTIC_MIME_PART_BYTES:
                return False
            decoded_total += len(decoded)
            if decoded_total > _MAX_SEMANTIC_MIME_TOTAL_BYTES:
                return False
            charset = part.get_content_charset() or "utf-8"
            if type(charset) is not str or not charset:
                return False
            text = decoded.decode(charset, errors="strict")
        except Exception:
            return False
        if text.strip():
            (html_parts if content_type == "text/html" else plain_parts).append(text)
        return True

    if not walk(message, depth=0, inside_attachment=False):
        return None
    selected = html_parts if html_parts else plain_parts
    result = "\n".join(selected)
    return result if result.strip() else None


def load_authorized_imap_incoming(
    headers,
    authority: PriorityAuthority,
    *,
    provider_folder: object,
    uid_validity: object,
    imap_uid: object,
) -> AuthorizedSemanticSource:
    if authority.provider != "custom_imap":
        raise SemanticAuthorityError("provider_mismatch", 400)
    if (
        not _valid_imap_folder(provider_folder)
        or not _valid_imap_number(uid_validity)
        or not _valid_imap_number(imap_uid)
    ):
        raise SemanticAuthorityError("invalid_incoming_locator", 400)
    resolution = resolve_authenticated_imap_mailbox(
        headers,
        authority.mailbox_id,
        require_smtp=False,
    )
    if resolution.get("status") != "ok" or type(resolution.get("mailbox")) is not dict:
        error = resolution.get("error") if type(resolution.get("error")) is dict else {}
        status = int(error.get("status_code") or 503)
        raise SemanticAuthorityError("imap_authority_unavailable", status)
    mailbox = resolution["mailbox"]

    connection = _open_authenticated_imap(mailbox)
    try:
        source_result = read_imap_reply_source(
            connection,
            folder=provider_folder,
            uid=imap_uid,
            expected_uid_validity=uid_validity,
        )
        if source_result.get("ok") is not True or type(source_result.get("source")) is not dict:
            error = source_result.get("error") if type(source_result.get("error")) is dict else {}
            code = error.get("code")
            status = 409 if code in {
                "uid_validity_changed",
                "message_not_found",
                "imap_reply_source_unthreadable",
            } else 503
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", status)
        source = source_result["source"]
        if (
            source.get("providerFolder") != provider_folder
            or source.get("uidValidity") != uid_validity
            or source.get("imapUid") != imap_uid
        ):
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
        root_message_id = _root_from_imap_source(source)
        latest_turn_id = normalize_message_id_token(source.get("messageId"))
        if root_message_id is None or latest_turn_id is None:
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
        thread_id = custom_imap_thread_id(authority.mailbox_id, root_message_id)
        conversation_id = canonical_conversation_id(authority.mailbox_id, thread_id)

        try:
            fetch_response = connection.uid(
                "FETCH",
                imap_uid,
                "(UID INTERNALDATE BODY.PEEK[])",
            )
        except Exception:
            raise SemanticAuthorityError("imap_unavailable", 503) from None
        if _is_absent_uid_fetch_response(fetch_response):
            raise SemanticAuthorityError("incoming_message_not_found", 404)
        parsed_fetch = _parse_imap_semantic_fetch(
            fetch_response,
            expected_uid=imap_uid,
        )
        if parsed_fetch is None:
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
        raw_message, occurred_at, provider_timestamp = parsed_fetch
        try:
            message = BytesParser().parsebytes(raw_message)
            body_text = _extract_semantic_mime_body(message)
            from_headers = message.get_all("From", [])
            if (
                type(from_headers) is not list
                or len(from_headers) != 1
                or type(from_headers[0]) is not str
            ):
                raise ValueError("invalid From authority")
            from_header = from_headers[0]
        except Exception:
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409) from None
        if body_text is None:
            raise SemanticAuthorityError("incoming_message_text_unavailable", 422)
        raw_identity = _raw_imap_thread_identity(message, imap_uid=imap_uid)
        if raw_identity != (latest_turn_id, root_message_id):
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)

        latest_result = read_imap_latest_thread_identity(
            connection,
            mailbox_key=authority.mailbox_id,
            folder=provider_folder,
            expected_uid_validity=uid_validity,
            target_uid=imap_uid,
            expected_thread_id=thread_id,
            require_predecessor=True,
        )
        latest = latest_result.get("latest") if type(latest_result) is dict else None
        if (
            latest_result.get("ok") is not True
            or type(latest) is not dict
            or latest.get("providerFolder") != provider_folder
            or latest.get("uidValidity") != uid_validity
            or latest.get("imapUid") != imap_uid
            or latest.get("threadId") != thread_id
            or latest.get("rfcMessageId") != latest_turn_id
        ):
            raise SemanticAuthorityError("incoming_message_stale", 409)
    finally:
        _safe_close_imap(connection)

    sender_email = _single_header_address(from_header)
    if sender_email is None or sender_email in authority.owned_emails:
        raise SemanticAuthorityError("incoming_message_not_external", 409)
    return AuthorizedSemanticSource(
        authority=authority,
        conversation_id=conversation_id,
        provider_conversation_id=root_message_id,
        latest_turn_id=latest_turn_id,
        occurred_at=occurred_at,
        turns=(
            {
                "turnId": latest_turn_id,
                "speaker": "external",
                "direction": "incoming",
                "text": body_text,
                "timestamp": provider_timestamp,
            },
        ),
        revalidation_locator={
            "provider": "custom_imap",
            "providerFolder": provider_folder,
            "uidValidity": uid_validity,
            "imapUid": imap_uid,
            "rfcMessageId": latest_turn_id,
        },
    )


def _resolve_imap_mailbox_for_authority(headers, authority: PriorityAuthority) -> dict:
    resolution = resolve_authenticated_imap_mailbox(
        headers,
        authority.mailbox_id,
        require_smtp=False,
    )
    mailbox = resolution.get("mailbox")
    if resolution.get("status") != "ok" or type(mailbox) is not dict:
        raise SemanticAuthorityError("imap_authority_unavailable", 503)
    return mailbox


def prove_authorized_imap_latest(
    headers,
    authority: PriorityAuthority,
    *,
    provider_conversation_id: str,
    conversation_id: str,
    provider_folder: str,
    uid_validity: str,
    target_uid: str,
    expected_rfc_message_id: str | None = None,
) -> None:
    if authority.provider != "custom_imap":
        raise SemanticAuthorityError("provider_mismatch", 400)
    if (
        not _valid_imap_folder(provider_folder)
        or not _valid_imap_number(uid_validity)
        or not _valid_imap_number(target_uid)
    ):
        raise SemanticAuthorityError("invalid_incoming_locator", 400)
    normalized_root = normalize_message_id_token(provider_conversation_id)
    if normalized_root is None:
        raise SemanticAuthorityError("conversation_mismatch", 409)
    expected_thread_id = custom_imap_thread_id(authority.mailbox_id, normalized_root)
    if canonical_conversation_id(
        authority.mailbox_id,
        expected_thread_id,
    ) != conversation_id:
        raise SemanticAuthorityError("conversation_mismatch", 409)
    mailbox = _resolve_imap_mailbox_for_authority(headers, authority)
    connection = _open_authenticated_imap(mailbox)
    try:
        result = read_imap_latest_thread_identity(
            connection,
            mailbox_key=authority.mailbox_id,
            folder=provider_folder,
            expected_uid_validity=uid_validity,
            target_uid=target_uid,
            expected_thread_id=expected_thread_id,
            require_predecessor=True,
        )
    finally:
        _safe_close_imap(connection)
    latest = result.get("latest") if type(result) is dict else None
    if (
        type(result) is not dict
        or
        result.get("ok") is not True
        or type(latest) is not dict
        or latest.get("providerFolder") != provider_folder
        or latest.get("uidValidity") != uid_validity
        or latest.get("imapUid") != target_uid
        or latest.get("threadId") != expected_thread_id
        or (
            expected_rfc_message_id is not None
            and latest.get("rfcMessageId") != expected_rfc_message_id
        )
    ):
        raise SemanticAuthorityError("incoming_message_stale", 409)


def prove_authorized_source_current(
    headers,
    source: AuthorizedSemanticSource,
    claims: OutgoingEventClaims | None,
) -> None:
    locator = source.revalidation_locator
    if type(locator) is not dict or locator.get("provider") != source.authority.provider:
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
    if source.authority.provider == "google":
        if claims is None:
            raise SemanticAuthorityError("event_scope_mismatch", 403)
        expected_message_id = locator.get("providerMessageId")
        if type(expected_message_id) is not str:
            raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
        prove_authorized_gmail_latest(
            source.authority,
            claims,
            expected_message_id=expected_message_id,
        )
        return
    provider_folder = locator.get("providerFolder")
    uid_validity = locator.get("uidValidity")
    target_uid = locator.get("imapUid")
    expected_rfc_message_id = locator.get("rfcMessageId")
    if not all(type(value) is str for value in (provider_folder, uid_validity, target_uid)):
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
    if expected_rfc_message_id is not None and type(expected_rfc_message_id) is not str:
        raise SemanticAuthorityError("incoming_message_identity_unconfirmed", 409)
    prove_authorized_imap_latest(
        headers,
        source.authority,
        provider_conversation_id=source.provider_conversation_id,
        conversation_id=source.conversation_id,
        provider_folder=provider_folder,
        uid_validity=uid_validity,
        target_uid=target_uid,
        expected_rfc_message_id=expected_rfc_message_id,
    )
