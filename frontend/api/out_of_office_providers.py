from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime
from http.client import IncompleteRead
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api.auth.email_address import normalize_auth_email
from api.inboxes.imap_snapshot import _parse_uid_search_response
from api.inboxes.imap_uid_validity import read_selected_mailbox_uid_validity
from api.inboxes.mailbox_secret_store import (
    is_valid_mailbox_credential_version,
    read_mailbox_secret,
)
from api.inboxes.oauth_token_store import (
    load_google_token_record_with_metadata,
    refresh_google_token_record,
)
from api.inboxes.smtp_connection import (
    SmtpConnectionError,
    send_public_smtp_message,
)
from api.out_of_office_store import (
    GmailOutOfOfficeCursor,
    ImapOutOfOfficeCursor,
    OutOfOfficeCursor,
    OutOfOfficeSettings,
    build_gmail_out_of_office_cursor,
    build_imap_out_of_office_cursor,
)
from api.out_of_office_worker import (
    InboundAutoReplyCandidate,
    OutOfOfficeCursorReset,
    OutOfOfficeProviderError,
    ProviderBatch,
)
from api.user_config_store import (
    read_user_config_record,
    resolve_managed_inbox,
    resolve_user_config_store,
)
from imap_connect_preview import connect_mailbox_with_settings


GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_FULL_SCOPE = "https://mail.google.com/"
MAX_GMAIL_RESPONSE_BYTES = 1024 * 1024
MAX_GMAIL_HISTORY_PAGES = 20
MAX_GMAIL_CANDIDATES_PER_RUN = 1000
MAX_IMAP_CANDIDATES_PER_RUN = 100
_UIDNEXT_PATTERN = re.compile(r"[1-9][0-9]{0,19}", re.ASCII)
_INTERNALDATE_PATTERN = re.compile(
    rb'\bINTERNALDATE\s+"([^"\r\n]{1,64})"',
    re.IGNORECASE,
)
_OOO_HEADER_FETCH_PATTERN = re.compile(
    rb'\A([1-9][0-9]*) \(UID ([1-9][0-9]*) INTERNALDATE "([^"\r\n]{1,64})" '
    rb'BODY\[HEADER\.FIELDS \([A-Za-z0-9 -]{1,512}\)\] '
    rb'\{(0|[1-9][0-9]*)\}(\))?\Z',
    re.ASCII,
)
_SAFE_RFC_MESSAGE_ID = re.compile(r"<[^\r\n<>]{1,990}>", re.ASCII)
_HEADER_NAMES = (
    "From",
    "Subject",
    "Auto-Submitted",
    "Precedence",
    "List-Id",
    "List-Unsubscribe",
    "X-Auto-Response-Suppress",
    "X-Autoreply",
    "X-Autorespond",
    "Return-Path",
    "Message-ID",
)


class GmailHttpError(Exception):
    __slots__ = ("status_code",)

    def __init__(self, status_code: int):
        super().__init__(str(status_code))
        self.status_code = status_code


def _read_bounded_response(response, maximum_bytes: int = MAX_GMAIL_RESPONSE_BYTES) -> bytes:
    body = response.read(maximum_bytes + 1)
    if len(body) > maximum_bytes:
        raise OutOfOfficeProviderError("gmail_response_too_large")
    return body


def _gmail_http_json(
    access_token: str,
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    if not isinstance(url, str) or not (
        url == f"{GMAIL_API_BASE_URL}/profile"
        or url.startswith(f"{GMAIL_API_BASE_URL}/history?")
        or url.startswith(f"{GMAIL_API_BASE_URL}/messages/")
    ):
        raise OutOfOfficeProviderError("gmail_request_invalid")

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {access_token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = _read_bounded_response(response)
    except HTTPError as error:
        raise GmailHttpError(error.code) from None
    except (IncompleteRead, OSError, URLError, TimeoutError):
        raise OutOfOfficeProviderError("gmail_unavailable") from None

    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise OutOfOfficeProviderError("gmail_response_invalid") from None
    if not isinstance(decoded, dict):
        raise OutOfOfficeProviderError("gmail_response_invalid")
    return decoded


def _token_expired(record: dict, *, now: datetime | None = None) -> bool:
    expires_at = record.get("expires_at")
    if expires_at is None:
        return False
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise OutOfOfficeProviderError("gmail_reconnect_required")
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        raise OutOfOfficeProviderError("gmail_reconnect_required") from None
    if parsed.tzinfo is None:
        raise OutOfOfficeProviderError("gmail_reconnect_required")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return parsed <= current.astimezone(timezone.utc)


def _scope_tokens(scope: object) -> set[str]:
    if not isinstance(scope, str):
        return set()
    return {token for token in scope.split() if token}


def _validate_google_token_record(
    record: object,
    *,
    mailbox_email: str,
    owner_email: str,
) -> dict:
    if not isinstance(record, dict):
        raise OutOfOfficeProviderError("gmail_reconnect_required")
    if (
        record.get("provider") != "google"
        or record.get("email") != mailbox_email
        or record.get("owner_email") != owner_email
        or record.get("_storage_durable") is not True
    ):
        raise OutOfOfficeProviderError("gmail_reconnect_required")

    access_token = record.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise OutOfOfficeProviderError("gmail_reconnect_required")

    scopes = _scope_tokens(record.get("scope"))
    if GMAIL_FULL_SCOPE not in scopes and (
        GMAIL_MODIFY_SCOPE not in scopes or GMAIL_SEND_SCOPE not in scopes
    ):
        raise OutOfOfficeProviderError("gmail_reconnect_required")
    return record


def _map_gmail_status(status_code: int) -> str:
    if status_code == 401:
        return "gmail_reconnect_required"
    if status_code == 403:
        return "gmail_permission_denied"
    if status_code == 429:
        return "gmail_rate_limited"
    if status_code >= 500:
        return "gmail_unavailable"
    return "gmail_request_failed"


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _gmail_internal_date_iso(value: object) -> str | None:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    try:
        milliseconds = int(value)
        parsed = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return _utc_iso(parsed)


def _parse_ooo_header_fetch_response(
    response: object,
    *,
    expected_uid: str,
) -> tuple[bytes, str] | None:
    if (
        not isinstance(response, tuple)
        or len(response) != 2
        or str(response[0]).upper() != "OK"
        or not isinstance(response[1], (list, tuple))
        or len(response[1]) not in (1, 2)
    ):
        return None
    literal = response[1][0]
    if not isinstance(literal, tuple) or len(literal) != 2:
        return None
    metadata, raw_headers = literal
    if not isinstance(metadata, bytes) or not isinstance(raw_headers, bytes):
        return None
    if len(metadata) > 4096 or len(raw_headers) > 64 * 1024:
        return None
    match = _OOO_HEADER_FETCH_PATTERN.fullmatch(metadata)
    if match is None:
        return None
    sequence_number, fetched_uid, internal_date, literal_size_text, inline_close = match.groups()
    try:
        sequence_text = sequence_number.decode("ascii")
        uid_text = fetched_uid.decode("ascii")
        literal_size = int(literal_size_text.decode("ascii"))
        internal_date_text = internal_date.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        not sequence_text.isdigit()
        or sequence_text.startswith("0")
        or uid_text != expected_uid
        or not uid_text.isdigit()
        or uid_text.startswith("0")
        or literal_size != len(raw_headers)
    ):
        return None
    if len(response[1]) == 1:
        if inline_close != b")":
            return None
    elif inline_close is not None or response[1][1] not in (b")", ")"):
        return None
    try:
        parsed = parsedate_to_datetime(internal_date_text)
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed.tzinfo is None:
        return None
    return raw_headers, _utc_iso(parsed)


def _safe_rfc_message_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _SAFE_RFC_MESSAGE_ID.fullmatch(text) else None


def _build_auto_reply_message(
    mailbox_email: str,
    candidate: InboundAutoReplyCandidate,
    settings: OutOfOfficeSettings,
) -> EmailMessage:
    sender = normalize_auth_email(candidate["senderEmail"])
    mailbox = normalize_auth_email(mailbox_email)
    if not sender or not mailbox:
        raise OutOfOfficeProviderError("auto_reply_address_invalid")

    message = EmailMessage()
    message["From"] = mailbox
    message["To"] = sender
    message["Subject"] = settings["subject"]
    message["Auto-Submitted"] = "auto-replied"
    message["X-Auto-Response-Suppress"] = "All"
    message["X-Autoreply"] = "yes"

    source_message_id = _safe_rfc_message_id(candidate.get("rfcMessageId"))
    if source_message_id:
        message["In-Reply-To"] = source_message_id
        message["References"] = source_message_id

    message.set_content(settings["message"])
    return message


def _extract_candidate_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, list) or len(headers) > 128:
        raise OutOfOfficeProviderError("provider_message_malformed")

    result: dict[str, str] = {}
    accepted = {name.lower() for name in _HEADER_NAMES}
    for raw in headers:
        if not isinstance(raw, dict):
            raise OutOfOfficeProviderError("provider_message_malformed")
        name = raw.get("name")
        value = raw.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise OutOfOfficeProviderError("provider_message_malformed")
        key = name.lower()
        if key in accepted and key not in result:
            if len(value) > 32_768 or "\x00" in value:
                raise OutOfOfficeProviderError("provider_message_malformed")
            result[key] = value
    return result


def _single_sender(from_header: str) -> str | None:
    addresses = {
        normalized
        for _display, address in getaddresses([from_header])
        if (normalized := normalize_auth_email(address))
    }
    if len(addresses) != 1:
        return None
    return next(iter(addresses))


def load_out_of_office_mailbox(owner_email: str, mailbox_id: str) -> dict:
    owner = normalize_auth_email(owner_email)
    if not owner or not isinstance(mailbox_id, str) or not mailbox_id.strip():
        raise OutOfOfficeProviderError("mailbox_invalid")
    mailbox_id = mailbox_id.strip()

    store, store_error = resolve_user_config_store()
    if store_error or store is None:
        raise OutOfOfficeProviderError("user_config_unavailable")

    read_result = read_user_config_record(store, owner)
    if read_result.get("status") != "ok" or not isinstance(read_result.get("config"), dict):
        status = read_result.get("status")
        if status == "missing":
            raise OutOfOfficeProviderError("mailbox_not_found")
        raise OutOfOfficeProviderError("user_config_unavailable")

    config = read_result["config"]
    configured_owner = config.get("email")
    if configured_owner is not None and normalize_auth_email(configured_owner) != owner:
        raise OutOfOfficeProviderError("user_config_malformed")

    minimal = resolve_managed_inbox(config, mailbox_id)
    if minimal.get("status") != "ok":
        if minimal.get("status") == "not_found":
            raise OutOfOfficeProviderError("mailbox_not_found")
        raise OutOfOfficeProviderError("mailbox_malformed")

    matches = [
        inbox
        for inbox in config.get("managedInboxes", [])
        if isinstance(inbox, dict) and inbox.get("id") == mailbox_id
    ]
    if len(matches) != 1:
        raise OutOfOfficeProviderError("mailbox_malformed")

    mailbox = deepcopy(matches[0])
    if (
        mailbox.get("provider") not in {"google", "custom_imap"}
        or mailbox.get("connected") is not True
        or mailbox.get("connectionStatus") != "connected"
        or normalize_auth_email(mailbox.get("email")) is None
    ):
        raise OutOfOfficeProviderError("mailbox_reconnect_required")
    return mailbox


class GmailOutOfOfficeAdapter:
    provider = "google"

    def __init__(
        self,
        *,
        load_token=load_google_token_record_with_metadata,
        refresh_token=refresh_google_token_record,
        http_json=_gmail_http_json,
    ):
        self._load_token = load_token
        self._refresh_token = refresh_token
        self._http_json = http_json

    def _token_record(
        self,
        mailbox: dict,
        owner_email: str,
        *,
        force_refresh: bool = False,
        now: datetime | None = None,
    ) -> dict:
        mailbox_email = normalize_auth_email(mailbox.get("email"))
        owner = normalize_auth_email(owner_email)
        if not mailbox_email or not owner:
            raise OutOfOfficeProviderError("gmail_reconnect_required")

        if force_refresh:
            record, error = self._refresh_token(mailbox_email, owner_email=owner)
        else:
            record, error = self._load_token(mailbox_email, owner_email=owner)
        if error:
            code = error.get("code") if isinstance(error, dict) else None
            if code in {
                "gmail_token_store_unavailable",
                "token_persistence_failed",
                "gmail_refresh_network_failed",
                "gmail_refresh_rate_limited",
                "gmail_refresh_provider_error",
            }:
                raise OutOfOfficeProviderError("gmail_unavailable")
            raise OutOfOfficeProviderError("gmail_reconnect_required")

        record = _validate_google_token_record(
            record,
            mailbox_email=mailbox_email,
            owner_email=owner,
        )
        if not force_refresh and _token_expired(record, now=now):
            return self._token_record(
                mailbox,
                owner,
                force_refresh=True,
                now=now,
            )
        return record

    def _request(
        self,
        mailbox: dict,
        owner_email: str,
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        now: datetime | None = None,
    ) -> dict:
        record = self._token_record(mailbox, owner_email, now=now)
        try:
            return self._http_json(
                record["access_token"].strip(),
                url,
                method=method,
                payload=payload,
            )
        except GmailHttpError as error:
            if error.status_code != 401:
                raise
        refreshed = self._token_record(
            mailbox,
            owner_email,
            force_refresh=True,
            now=now,
        )
        return self._http_json(
            refreshed["access_token"].strip(),
            url,
            method=method,
            payload=payload,
        )

    def baseline(
        self,
        mailbox: dict,
        *,
        owner_email: str,
        now: datetime,
    ) -> GmailOutOfOfficeCursor:
        try:
            payload = self._request(
                mailbox,
                owner_email,
                f"{GMAIL_API_BASE_URL}/profile",
                now=now,
            )
        except GmailHttpError as error:
            raise OutOfOfficeProviderError(_map_gmail_status(error.status_code)) from None
        history_id = payload.get("historyId")
        try:
            return build_gmail_out_of_office_cursor(str(history_id), now=now)
        except Exception:
            raise OutOfOfficeProviderError("gmail_response_invalid") from None

    def fetch_since(
        self,
        mailbox: dict,
        cursor: OutOfOfficeCursor,
        *,
        owner_email: str,
        now: datetime,
    ) -> ProviderBatch:
        if cursor.get("provider") != "google" or not isinstance(cursor.get("historyId"), str):
            raise OutOfOfficeProviderError("cursor_invalid")

        message_ids: list[str] = []
        seen_ids: set[str] = set()
        page_token: str | None = None
        final_history_id: str | None = None

        for _page_index in range(MAX_GMAIL_HISTORY_PAGES):
            query: list[tuple[str, str]] = [
                ("startHistoryId", cursor["historyId"]),
                ("historyTypes", "messageAdded"),
                ("labelId", "INBOX"),
                ("maxResults", "500"),
            ]
            if page_token:
                query.append(("pageToken", page_token))
            url = f"{GMAIL_API_BASE_URL}/history?{urlencode(query)}"
            try:
                payload = self._request(mailbox, owner_email, url, now=now)
            except GmailHttpError as error:
                if error.status_code == 404:
                    raise OutOfOfficeCursorReset(
                        self.baseline(mailbox, owner_email=owner_email, now=now)
                    ) from None
                raise OutOfOfficeProviderError(_map_gmail_status(error.status_code)) from None

            history = payload.get("history", [])
            if not isinstance(history, list):
                raise OutOfOfficeProviderError("gmail_response_invalid")
            for entry in history:
                if not isinstance(entry, dict):
                    raise OutOfOfficeProviderError("gmail_response_invalid")
                added = entry.get("messagesAdded", [])
                if not isinstance(added, list):
                    raise OutOfOfficeProviderError("gmail_response_invalid")
                for item in added:
                    if not isinstance(item, dict) or not isinstance(item.get("message"), dict):
                        raise OutOfOfficeProviderError("gmail_response_invalid")
                    message_id = item["message"].get("id")
                    if (
                        isinstance(message_id, str)
                        and message_id
                        and len(message_id) <= 256
                        and message_id not in seen_ids
                    ):
                        seen_ids.add(message_id)
                        message_ids.append(message_id)
                        if len(message_ids) > MAX_GMAIL_CANDIDATES_PER_RUN:
                            raise OutOfOfficeProviderError("gmail_history_backlog_too_large")

            history_id = payload.get("historyId")
            if not isinstance(history_id, str) or not history_id.isdigit():
                raise OutOfOfficeProviderError("gmail_response_invalid")
            final_history_id = history_id

            next_page = payload.get("nextPageToken")
            if next_page is None:
                page_token = None
                break
            if not isinstance(next_page, str) or not next_page or len(next_page) > 1024:
                raise OutOfOfficeProviderError("gmail_response_invalid")
            page_token = next_page
        else:
            raise OutOfOfficeProviderError("gmail_history_backlog_too_large")

        if page_token is not None or final_history_id is None:
            raise OutOfOfficeProviderError("gmail_history_backlog_too_large")

        candidates: list[InboundAutoReplyCandidate] = []
        metadata_names = [("metadataHeaders", name) for name in _HEADER_NAMES]
        for message_id in message_ids:
            query = urlencode(
                [("format", "metadata"), *metadata_names],
                doseq=True,
            )
            url = (
                f"{GMAIL_API_BASE_URL}/messages/{message_id}?"
                + query
            )
            try:
                payload = self._request(mailbox, owner_email, url, now=now)
            except GmailHttpError as error:
                if error.status_code == 404:
                    continue
                raise OutOfOfficeProviderError(_map_gmail_status(error.status_code)) from None

            label_ids = payload.get("labelIds")
            if not isinstance(label_ids, list) or "INBOX" not in label_ids:
                continue
            raw_payload = payload.get("payload")
            headers = (
                raw_payload.get("headers")
                if isinstance(raw_payload, dict)
                else None
            )
            normalized_headers = _extract_candidate_headers(headers)
            sender = _single_sender(normalized_headers.get("from", ""))
            if not sender:
                continue
            received_at = _gmail_internal_date_iso(payload.get("internalDate"))
            if received_at is None:
                continue
            candidates.append(
                {
                    "providerMessageId": message_id,
                    "senderEmail": sender,
                    "rfcMessageId": _safe_rfc_message_id(
                        normalized_headers.get("message-id")
                    ),
                    "receivedAt": received_at,
                    "headers": normalized_headers,
                }
            )

        return {
            "cursor": build_gmail_out_of_office_cursor(final_history_id, now=now),
            "candidates": candidates,
        }

    def send_reply(
        self,
        mailbox: dict,
        candidate: InboundAutoReplyCandidate,
        settings: OutOfOfficeSettings,
        *,
        owner_email: str,
    ) -> None:
        mailbox_email = normalize_auth_email(mailbox.get("email"))
        if not mailbox_email:
            raise OutOfOfficeProviderError("gmail_reconnect_required")
        message = _build_auto_reply_message(mailbox_email, candidate, settings)
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        try:
            payload = self._request(
                mailbox,
                owner_email,
                f"{GMAIL_API_BASE_URL}/messages/send",
                method="POST",
                payload={"raw": encoded},
            )
        except GmailHttpError as error:
            raise OutOfOfficeProviderError(_map_gmail_status(error.status_code)) from None
        if not isinstance(payload.get("id"), str) or not payload["id"]:
            raise OutOfOfficeProviderError("gmail_send_failed")


def _exact_string(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _port(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 1 <= value <= 65535 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
        return parsed if 1 <= parsed <= 65535 else None
    return None


def _resolve_imap_runtime(
    mailbox: dict,
    *,
    owner_email: str,
    read_secret=read_mailbox_secret,
) -> dict:
    if (
        mailbox.get("provider") != "custom_imap"
        or mailbox.get("connected") is not True
        or mailbox.get("connectionStatus") != "connected"
        or mailbox.get("imapConnectionStatus") != "connected"
        or mailbox.get("smtpConnectionStatus") != "connected"
        or mailbox.get("fullyConnected") is not True
    ):
        raise OutOfOfficeProviderError("imap_reconnect_required")

    mailbox_id = _exact_string(mailbox.get("id"))
    email = normalize_auth_email(mailbox.get("email"))
    custom_imap = mailbox.get("customImap")
    custom_smtp = mailbox.get("customSmtp")
    credential_version = mailbox.get("credentialVersion")
    if (
        not mailbox_id
        or not email
        or not isinstance(custom_imap, dict)
        or not isinstance(custom_smtp, dict)
        or not is_valid_mailbox_credential_version(credential_version)
    ):
        raise OutOfOfficeProviderError("imap_reconnect_required")

    imap_host = _exact_string(custom_imap.get("host"))
    imap_port = _port(custom_imap.get("port"))
    imap_username = _exact_string(custom_imap.get("username")) or email
    if not imap_host or not imap_port or custom_imap.get("ssl") is not True or not imap_username:
        raise OutOfOfficeProviderError("imap_reconnect_required")

    smtp_host = _exact_string(custom_smtp.get("host"))
    smtp_port = _port(custom_smtp.get("port"))
    smtp_security = custom_smtp.get("security")
    use_same_credentials = custom_smtp.get("useSameCredentials") is True
    smtp_username = (
        imap_username
        if use_same_credentials
        else _exact_string(custom_smtp.get("username"))
    )
    if (
        not smtp_host
        or not smtp_port
        or smtp_security not in {"ssl", "starttls"}
        or (smtp_security == "ssl" and smtp_port != 465)
        or (smtp_security == "starttls" and smtp_port != 587)
        or not smtp_username
    ):
        raise OutOfOfficeProviderError("imap_reconnect_required")

    secret_result = read_secret(owner_email, mailbox_id)
    if not isinstance(secret_result, dict) or secret_result.get("status") != "present":
        status = secret_result.get("status") if isinstance(secret_result, dict) else None
        if status == "unavailable":
            raise OutOfOfficeProviderError("mailbox_secret_unavailable")
        raise OutOfOfficeProviderError("imap_reconnect_required")
    record = secret_result.get("record")
    if not isinstance(record, dict):
        raise OutOfOfficeProviderError("imap_reconnect_required")
    if (
        record.get("credentialVersion") != credential_version
        or not is_valid_mailbox_credential_version(record.get("credentialVersion"))
    ):
        raise OutOfOfficeProviderError("imap_reconnect_required")

    imap_password = record.get("imapPassword")
    smtp_password = imap_password if use_same_credentials else record.get("smtpPassword")
    if not isinstance(imap_password, str) or not imap_password:
        raise OutOfOfficeProviderError("imap_reconnect_required")
    if not isinstance(smtp_password, str) or not smtp_password:
        raise OutOfOfficeProviderError("imap_reconnect_required")

    return {
        "mailboxId": mailbox_id,
        "email": email,
        "imap": {
            "host": imap_host,
            "port": imap_port,
            "username": imap_username,
            "password": imap_password,
        },
        "smtp": {
            "host": smtp_host,
            "port": smtp_port,
            "security": smtp_security,
            "username": smtp_username,
            "password": smtp_password,
        },
    }


def _select_inbox(mailbox) -> None:
    try:
        response = mailbox.select("INBOX", readonly=True)
    except Exception:
        raise OutOfOfficeProviderError("imap_unavailable") from None
    if (
        not isinstance(response, tuple)
        or len(response) != 2
        or str(response[0]).upper().replace("B'", "").replace("'", "") != "OK"
    ):
        raise OutOfOfficeProviderError("imap_unavailable")


def _read_uid_next(mailbox) -> str:
    try:
        tag, values = mailbox.response("UIDNEXT")
    except Exception:
        raise OutOfOfficeProviderError("imap_unavailable") from None
    if tag != "UIDNEXT" or not isinstance(values, (list, tuple)) or len(values) != 1:
        raise OutOfOfficeProviderError("imap_unavailable")
    raw = values[0]
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError:
            raise OutOfOfficeProviderError("imap_unavailable") from None
    if not isinstance(raw, str) or _UIDNEXT_PATTERN.fullmatch(raw) is None:
        raise OutOfOfficeProviderError("imap_unavailable")
    return raw


def _safe_logout(mailbox) -> None:
    try:
        mailbox.logout()
    except Exception:
        try:
            mailbox.shutdown()
        except Exception:
            pass


def _parse_imap_candidate(
    uid: str,
    raw_headers: bytes,
    *,
    received_at: str,
) -> InboundAutoReplyCandidate | None:
    try:
        message = BytesHeaderParser(policy=policy.default).parsebytes(raw_headers)
    except Exception:
        raise OutOfOfficeProviderError("imap_message_malformed") from None

    normalized_headers: dict[str, str] = {}
    for name in _HEADER_NAMES:
        value = message.get(name)
        if value is not None:
            text = str(value)
            if len(text) > 32_768 or "\x00" in text:
                raise OutOfOfficeProviderError("imap_message_malformed")
            normalized_headers[name.lower()] = text

    sender = _single_sender(normalized_headers.get("from", ""))
    if not sender:
        return None
    return {
        "providerMessageId": uid,
        "senderEmail": sender,
        "rfcMessageId": _safe_rfc_message_id(normalized_headers.get("message-id")),
        "receivedAt": received_at,
        "headers": normalized_headers,
    }


class ImapOutOfOfficeAdapter:
    provider = "custom_imap"

    def __init__(
        self,
        *,
        read_secret=read_mailbox_secret,
        connect_imap=connect_mailbox_with_settings,
        send_smtp=send_public_smtp_message,
    ):
        self._read_secret = read_secret
        self._connect_imap = connect_imap
        self._send_smtp = send_smtp

    def _runtime(self, mailbox: dict, owner_email: str) -> dict:
        return _resolve_imap_runtime(
            mailbox,
            owner_email=owner_email,
            read_secret=self._read_secret,
        )

    def _open(self, runtime: dict):
        imap = runtime["imap"]
        try:
            mailbox = self._connect_imap(
                imap["host"],
                imap["port"],
                imap["username"],
                imap["password"],
                True,
                timeout=30,
            )
        except Exception:
            raise OutOfOfficeProviderError("imap_unavailable") from None
        _select_inbox(mailbox)
        return mailbox

    def baseline(
        self,
        mailbox: dict,
        *,
        owner_email: str,
        now: datetime,
    ) -> ImapOutOfOfficeCursor:
        runtime = self._runtime(mailbox, owner_email)
        connection = self._open(runtime)
        try:
            uid_validity = read_selected_mailbox_uid_validity(connection)
            if uid_validity is None:
                raise OutOfOfficeProviderError("imap_unavailable")
            uid_next = _read_uid_next(connection)
            last_uid = str(max(0, int(uid_next) - 1))
            return build_imap_out_of_office_cursor(
                uid_validity,
                last_uid,
                now=now,
            )
        finally:
            _safe_logout(connection)

    def fetch_since(
        self,
        mailbox: dict,
        cursor: OutOfOfficeCursor,
        *,
        owner_email: str,
        now: datetime,
    ) -> ProviderBatch:
        if (
            cursor.get("provider") != "custom_imap"
            or not isinstance(cursor.get("uidValidity"), str)
            or not isinstance(cursor.get("lastUid"), str)
        ):
            raise OutOfOfficeProviderError("cursor_invalid")

        runtime = self._runtime(mailbox, owner_email)
        connection = self._open(runtime)
        try:
            uid_validity = read_selected_mailbox_uid_validity(connection)
            if uid_validity is None:
                raise OutOfOfficeProviderError("imap_unavailable")
            uid_next = _read_uid_next(connection)
            max_assigned_uid = max(0, int(uid_next) - 1)

            if uid_validity != cursor["uidValidity"]:
                raise OutOfOfficeCursorReset(
                    build_imap_out_of_office_cursor(
                        uid_validity,
                        str(max_assigned_uid),
                        now=now,
                    )
                )

            last_uid = int(cursor["lastUid"])
            if max_assigned_uid <= last_uid:
                return {
                    "cursor": build_imap_out_of_office_cursor(
                        uid_validity,
                        str(last_uid),
                        now=now,
                    ),
                    "candidates": [],
                }

            start_uid = last_uid + 1
            try:
                search_response = connection.uid(
                    "SEARCH",
                    None,
                    "UID",
                    f"{start_uid}:{max_assigned_uid}",
                )
            except Exception:
                raise OutOfOfficeProviderError("imap_unavailable") from None
            uids = _parse_uid_search_response(search_response)
            if uids is None:
                raise OutOfOfficeProviderError("imap_unavailable")

            selected_uids = uids[:MAX_IMAP_CANDIDATES_PER_RUN]
            cursor_uid = (
                int(selected_uids[-1])
                if selected_uids
                else max_assigned_uid
            )
            candidates: list[InboundAutoReplyCandidate] = []
            fetch_fields = " ".join(_HEADER_NAMES)
            for uid in selected_uids:
                try:
                    fetch_response = connection.uid(
                        "FETCH",
                        uid,
                        f"(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fetch_fields})])",
                    )
                except Exception:
                    raise OutOfOfficeProviderError("imap_unavailable") from None
                parsed_fetch = _parse_ooo_header_fetch_response(
                    fetch_response,
                    expected_uid=uid,
                )
                if parsed_fetch is None:
                    if (
                        isinstance(fetch_response, tuple)
                        and len(fetch_response) == 2
                        and str(fetch_response[0]).upper() == "OK"
                        and fetch_response[1] in (None, [], [None])
                    ):
                        continue
                    raise OutOfOfficeProviderError("imap_message_malformed")
                raw, received_at = parsed_fetch
                candidate = _parse_imap_candidate(
                    uid,
                    raw,
                    received_at=received_at,
                )
                if candidate is not None:
                    candidates.append(candidate)

            return {
                "cursor": build_imap_out_of_office_cursor(
                    uid_validity,
                    str(cursor_uid),
                    now=now,
                ),
                "candidates": candidates,
            }
        finally:
            _safe_logout(connection)

    def send_reply(
        self,
        mailbox: dict,
        candidate: InboundAutoReplyCandidate,
        settings: OutOfOfficeSettings,
        *,
        owner_email: str,
    ) -> None:
        runtime = self._runtime(mailbox, owner_email)
        message = _build_auto_reply_message(runtime["email"], candidate, settings)
        smtp = runtime["smtp"]
        try:
            self._send_smtp(
                smtp["host"],
                smtp["port"],
                smtp["security"],
                smtp["username"],
                smtp["password"],
                message,
                [candidate["senderEmail"]],
                timeout=30,
            )
        except SmtpConnectionError as error:
            raise OutOfOfficeProviderError(error.code) from None
        except Exception:
            raise OutOfOfficeProviderError("smtp_send_failed") from None


_GMAIL_ADAPTER = GmailOutOfOfficeAdapter()
_IMAP_ADAPTER = ImapOutOfOfficeAdapter()


def resolve_out_of_office_adapter(mailbox: dict):
    provider = mailbox.get("provider") if isinstance(mailbox, dict) else None
    if provider == "google":
        return _GMAIL_ADAPTER
    if provider == "custom_imap":
        return _IMAP_ADAPTER
    raise OutOfOfficeProviderError("provider_unsupported")
