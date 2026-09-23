"""Bounded Gmail History API delta discovery through an injected request boundary.

This module performs provider reads only. It does not touch PostgreSQL, mutate
mailbox state, infer cache deletions, or activate any route. Callers receive a
bounded set of provider message IDs that changed after an already-persisted
Gmail history cursor and may then recover those messages exactly before planning
one transactional durable commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode


GmailRequestWithOneRefresh = Callable[
    [dict, str],
    tuple[dict | None, dict | None, dict, dict | None],
]

_PAGE_SIZE = 100
_MAX_HISTORY_PAGES = 10
_MAX_HISTORY_RECORDS = 1_000
_MAX_AFFECTED_MESSAGE_IDS = 100
_MAX_PAGE_TOKEN_BYTES = 4_096
_MAX_PROVIDER_MESSAGE_ID_BYTES = 1_024

_TYPED_HISTORY_FIELDS = (
    "messagesAdded",
    "messagesDeleted",
    "labelsAdded",
    "labelsRemoved",
)


@dataclass(frozen=True, slots=True)
class GmailHistoryDeltaResult:
    status: str
    context: dict
    next_history_id: str | None
    affected_message_ids: tuple[str, ...]
    page_count: int
    history_record_count: int


def _valid_history_id(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and value.isascii()
        and value.isdigit()
    )


def _valid_page_token(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return 1 <= len(encoded) <= _MAX_PAGE_TOKEN_BYTES


def _valid_provider_message_id(value: object) -> bool:
    if type(value) is not str or not value or not value.isascii():
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeError:
        return False
    return (
        1 <= len(encoded) <= _MAX_PROVIDER_MESSAGE_ID_BYTES
        and not any(character.isspace() or ord(character) < 33 for character in value)
    )


def _history_path(start_history_id: str, page_token: str | None) -> str:
    query: list[tuple[str, object]] = [
        ("startHistoryId", start_history_id),
        ("maxResults", _PAGE_SIZE),
    ]
    if page_token is not None:
        query.append(("pageToken", page_token))
    return "/history?" + urlencode(query)


def _append_message_id(
    message: object,
    *,
    affected: list[str],
    seen: set[str],
) -> bool:
    if type(message) is not dict:
        return False
    provider_message_id = message.get("id")
    if not _valid_provider_message_id(provider_message_id):
        return False
    if provider_message_id not in seen:
        seen.add(provider_message_id)
        affected.append(provider_message_id)
    return True


def _collect_history_message_ids(
    record: object,
    *,
    affected: list[str],
    seen: set[str],
) -> bool:
    if type(record) is not dict or not _valid_history_id(record.get("id")):
        return False

    generic_messages = record.get("messages")
    if generic_messages is not None:
        if type(generic_messages) is not list:
            return False
        for message in generic_messages:
            if not _append_message_id(message, affected=affected, seen=seen):
                return False

    for field in _TYPED_HISTORY_FIELDS:
        entries = record.get(field)
        if entries is None:
            continue
        if type(entries) is not list:
            return False
        for entry in entries:
            if type(entry) is not dict:
                return False
            if not _append_message_id(
                entry.get("message"),
                affected=affected,
                seen=seen,
            ):
                return False

    return True


def _result(
    status: str,
    context: dict,
    *,
    next_history_id: str | None = None,
    affected_message_ids: tuple[str, ...] = (),
    page_count: int = 0,
    history_record_count: int = 0,
) -> GmailHistoryDeltaResult:
    return GmailHistoryDeltaResult(
        status=status,
        context=context,
        next_history_id=next_history_id,
        affected_message_ids=affected_message_ids,
        page_count=page_count,
        history_record_count=history_record_count,
    )


def read_gmail_history_delta(
    context: dict,
    *,
    start_history_id: str,
    request_with_one_refresh: GmailRequestWithOneRefresh,
) -> GmailHistoryDeltaResult:
    """Read a complete bounded Gmail history window after start_history_id.

    ok is returned only after the final page is read. The caller may advance
    its durable cursor to next_history_id only after every affected message has
    subsequently been recovered and committed. full_sync_required maps Gmail's
    documented stale-history 404 to an explicit recovery signal.
    """

    if (
        type(context) is not dict
        or not _valid_history_id(start_history_id)
        or not callable(request_with_one_refresh)
    ):
        raise ValueError("invalid Gmail history delta request")

    current_context = context
    affected: list[str] = []
    seen_message_ids: set[str] = set()
    seen_page_tokens: set[str] = set()
    page_token: str | None = None
    page_count = 0
    history_record_count = 0
    start_value = int(start_history_id)

    while page_count < _MAX_HISTORY_PAGES:
        payload, error, next_context, refresh_failure = request_with_one_refresh(
            current_context,
            _history_path(start_history_id, page_token),
        )
        if type(next_context) is dict:
            current_context = next_context

        if refresh_failure is not None:
            return _result(
                "unavailable",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )
        if error is not None:
            status = (
                "full_sync_required"
                if type(error) is dict
                and error.get("code") == "gmail_message_not_found"
                else "unavailable"
            )
            return _result(
                status,
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )
        if type(payload) is not dict:
            return _result(
                "invalid",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )

        response_history_id = payload.get("historyId")
        if (
            not _valid_history_id(response_history_id)
            or int(response_history_id) < start_value
        ):
            return _result(
                "invalid",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )

        records = payload.get("history", [])
        if type(records) is not list:
            return _result(
                "invalid",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )

        page_count += 1
        history_record_count += len(records)
        if history_record_count > _MAX_HISTORY_RECORDS:
            return _result(
                "overflow",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )

        maximum_record_history = start_value
        for record in records:
            if not _collect_history_message_ids(
                record,
                affected=affected,
                seen=seen_message_ids,
            ):
                return _result(
                    "invalid",
                    current_context,
                    page_count=page_count,
                    history_record_count=history_record_count,
                )
            maximum_record_history = max(
                maximum_record_history,
                int(record["id"]),
            )
            if len(affected) > _MAX_AFFECTED_MESSAGE_IDS:
                return _result(
                    "overflow",
                    current_context,
                    page_count=page_count,
                    history_record_count=history_record_count,
                )

        if int(response_history_id) < maximum_record_history:
            return _result(
                "invalid",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )

        next_page_token = payload.get("nextPageToken")
        if next_page_token is None:
            return _result(
                "ok",
                current_context,
                next_history_id=response_history_id,
                affected_message_ids=tuple(affected),
                page_count=page_count,
                history_record_count=history_record_count,
            )
        if (
            not _valid_page_token(next_page_token)
            or next_page_token in seen_page_tokens
        ):
            return _result(
                "invalid",
                current_context,
                page_count=page_count,
                history_record_count=history_record_count,
            )
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token

    return _result(
        "overflow",
        current_context,
        page_count=page_count,
        history_record_count=history_record_count,
    )


__all__ = (
    "GmailHistoryDeltaResult",
    "read_gmail_history_delta",
)
