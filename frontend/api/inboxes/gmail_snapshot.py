from __future__ import annotations

import base64
import binascii
import json
from email import message_from_bytes
from email.errors import MessageError
from typing import Callable
from urllib.parse import quote, urlencode

from .authenticated_gmail import (
    MAX_GMAIL_RESPONSE_BYTES,
    valid_identifier,
)

GMAIL_API_UID_VALIDITY = "gmail-api"
GMAIL_ARCHIVE_QUERY = (
    "-label:inbox -label:trash -label:spam -label:drafts -label:sent"
)
DEFAULT_GMAIL_SNAPSHOT_LIMIT = 50
MAX_GMAIL_SNAPSHOT_LIMIT = 100
_ARCHIVE_EXCLUDED_LABELS = {"INBOX", "TRASH", "SPAM", "DRAFT", "SENT"}

GmailRequestWithOneRefresh = Callable[
    [dict, str],
    tuple[dict | None, dict | None, dict, dict | None],
]


def _result(
    context: dict,
    *,
    snapshot: dict | None = None,
    error: dict | None = None,
    refresh_failure: dict | None = None,
) -> dict:
    return {
        "status": (
            "ok"
            if snapshot is not None
            and error is None
            and refresh_failure is None
            else "error"
        ),
        "context": context,
        "snapshot": snapshot,
        "error": error,
        "refresh_failure": refresh_failure,
    }


def _invalid_response(context: dict) -> dict:
    return _result(
        context,
        error={"code": "gmail_response_invalid"},
    )


def _base64url_decode(value: str) -> bytes:
    unpadded = value.rstrip("=")
    padding_count = len(value) - len(unpadded)
    if (
        not unpadded
        or padding_count > 2
        or padding_count not in {0, -len(unpadded) % 4}
        or "=" in unpadded
        or any(
            not (
                character.isascii()
                and (
                    character.isalnum()
                    or character in {"-", "_"}
                )
            )
            for character in unpadded
        )
        or len(unpadded) % 4 == 1
    ):
        raise ValueError("invalid base64url value")

    padding = "=" * (-len(unpadded) % 4)
    decoded = base64.b64decode(
        f"{unpadded}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
    if (
        base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        != unpadded
    ):
        raise ValueError("non-canonical base64url value")
    return decoded


def _list_path(provider_folder: str, limit: int) -> str:
    query = (
        {"labelIds": "INBOX", "maxResults": limit}
        if provider_folder == "Inbox"
        else {"q": GMAIL_ARCHIVE_QUERY, "maxResults": limit}
    )
    return f"/messages?{urlencode(query)}"


def _strict_labels_match_folder(
    label_ids: list[str],
    provider_folder: str,
) -> bool:
    normalized = {label_id.upper() for label_id in label_ids}
    if provider_folder == "Inbox":
        return "INBOX" in normalized
    return not normalized.intersection(_ARCHIVE_EXCLUDED_LABELS)


def _message_ids_from_list(
    list_payload: dict,
    *,
    strict: bool,
    limit: int,
) -> tuple[list[str] | None, bool]:
    raw_refs = list_payload.get("messages")
    if raw_refs is None:
        raw_refs = []
    if not isinstance(raw_refs, list):
        return None, False

    message_ids: list[str] = []
    seen: set[str] = set()
    for raw_ref in raw_refs[:limit]:
        message_id = raw_ref.get("id") if isinstance(raw_ref, dict) else None
        if not valid_identifier(message_id) or message_id in seen:
            if strict:
                return None, False
            continue
        seen.add(message_id)
        message_ids.append(message_id)
    return message_ids, True


def parse_gmail_message_detail(
    detail_payload: object,
    *,
    context: dict,
    provider_folder: str,
    requested_message_id: str,
    index: int,
    focus_preferences: dict | None = None,
    strict: bool = False,
    message_parser=message_from_bytes,
) -> dict | None:
    """Validate and normalize one Gmail ``format=raw`` detail response.

    This helper is transport-free. Documented provider-data failures return
    ``None``; unexpected parser or preview-building failures remain fatal so
    callers cannot accidentally publish a partial result.
    """

    if (
        provider_folder not in {"Inbox", "Archive"}
        or not valid_identifier(requested_message_id)
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or not callable(message_parser)
        or not isinstance(detail_payload, dict)
    ):
        return None

    provider_message_id = detail_payload.get("id")
    if (
        not valid_identifier(provider_message_id)
        or provider_message_id != requested_message_id
    ):
        return None

    raw_label_ids = detail_payload.get("labelIds", [])
    labels_are_valid = (
        isinstance(raw_label_ids, list)
        and all(valid_identifier(label_id) for label_id in raw_label_ids)
        and len(set(raw_label_ids)) == len(raw_label_ids)
    )
    if strict and (
        not labels_are_valid
        or not _strict_labels_match_folder(
            raw_label_ids,
            provider_folder,
        )
    ):
        return None
    label_ids = list(raw_label_ids) if labels_are_valid else []

    raw_message = detail_payload.get("raw")
    if not isinstance(raw_message, str) or not raw_message:
        return None
    try:
        decoded_message = _base64url_decode(raw_message)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None
    try:
        parsed_message = message_parser(decoded_message)
    except MessageError:
        return None

    import imap_connect_preview

    unread = "UNREAD" in label_ids
    flagged = "STARRED" in label_ids
    preview = imap_connect_preview.to_message_preview(
        parsed_message,
        index,
        context["mailbox_email"],
        unread,
        None,
        flagged,
        internal_role=None,
        focus_preferences=focus_preferences,
    )
    preview.pop("imapUid", None)
    preview["providerMessageId"] = provider_message_id

    provider_thread_id = detail_payload.get("threadId")
    if valid_identifier(provider_thread_id):
        preview["providerThreadId"] = provider_thread_id
    elif strict:
        return None

    preview["labelIds"] = label_ids
    preview["providerFolder"] = provider_folder
    preview["serverMailboxId"] = context["mailbox_id"]

    rfc_message_id = parsed_message.get("Message-Id")
    if isinstance(rfc_message_id, str):
        normalized_rfc_message_id = (
            rfc_message_id.strip().strip("<>").strip()
        )
        if valid_identifier(normalized_rfc_message_id):
            preview["rfcMessageId"] = normalized_rfc_message_id

    return preview


def read_gmail_folder_snapshot(
    context: dict,
    *,
    provider_folder: str,
    request_with_one_refresh: GmailRequestWithOneRefresh,
    limit: int = DEFAULT_GMAIL_SNAPSHOT_LIMIT,
    focus_preferences: dict | None = None,
    strict: bool = False,
    required_message_id: str | None = None,
    message_parser=message_from_bytes,
) -> dict:
    """Read and normalize one bounded Gmail folder snapshot.

    The injected request callback owns authenticated transport and the single
    permitted token-refresh attempt. Every return includes the latest context,
    a snapshot or provider error, and any structured refresh failure.
    """

    if (
        provider_folder not in {"Inbox", "Archive"}
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > MAX_GMAIL_SNAPSHOT_LIMIT
        or not callable(request_with_one_refresh)
        or (
            required_message_id is not None
            and (
                provider_folder != "Archive"
                or not valid_identifier(required_message_id)
            )
        )
    ):
        return _result(
            context,
            error={"code": "gmail_snapshot_invalid_request"},
        )

    list_payload, list_error, context, refresh_failure = (
        request_with_one_refresh(
            context,
            _list_path(provider_folder, limit),
        )
    )
    if refresh_failure is not None:
        return _result(
            context,
            error=list_error,
            refresh_failure=refresh_failure,
        )
    if list_error is not None:
        return _result(context, error=list_error)
    if not isinstance(list_payload, dict):
        return _invalid_response(context)

    message_ids, list_is_valid = _message_ids_from_list(
        list_payload,
        strict=strict,
        limit=limit,
    )
    if not list_is_valid or message_ids is None:
        return _invalid_response(context)

    if (
        required_message_id is not None
        and required_message_id not in message_ids
    ):
        if len(message_ids) >= limit:
            message_ids = message_ids[: limit - 1]
        message_ids.append(required_message_id)

    messages: list[dict] = []
    snapshot = {
        "providerFolder": provider_folder,
        "serverMailboxId": context.get("mailbox_id"),
        "messages": messages,
        "uidValidity": GMAIL_API_UID_VALIDITY,
    }
    for index, requested_message_id in enumerate(message_ids):
        detail_payload, detail_error, context, refresh_failure = (
            request_with_one_refresh(
                context,
                (
                    f"/messages/{quote(requested_message_id, safe='')}"
                    "?format=raw"
                ),
            )
        )
        if refresh_failure is not None:
            return _result(
                context,
                error=detail_error,
                refresh_failure=refresh_failure,
            )
        if detail_error is not None:
            return _result(context, error=detail_error)
        preview = parse_gmail_message_detail(
            detail_payload,
            context=context,
            provider_folder=provider_folder,
            requested_message_id=requested_message_id,
            index=index,
            focus_preferences=focus_preferences,
            strict=strict,
            message_parser=message_parser,
        )
        if preview is None:
            if strict:
                return _invalid_response(context)
            continue

        candidate_snapshot = {
            **snapshot,
            "messages": [*messages, preview],
        }
        try:
            candidate_size = len(
                json.dumps(candidate_snapshot).encode("utf-8")
            )
        except (TypeError, ValueError):
            raise
        if candidate_size > MAX_GMAIL_RESPONSE_BYTES:
            if strict:
                return _result(
                    context,
                    error={"code": "gmail_response_too_large"},
                )
            break
        messages.append(preview)

    return _result(context, snapshot=snapshot)
