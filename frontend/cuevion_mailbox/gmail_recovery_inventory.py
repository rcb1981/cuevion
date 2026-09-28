"""Bounded complete Gmail Inbox membership inventory for stale-History recovery.

This provider reader deliberately does not paginate past the recovery bound.
A result is usable for cursor recovery only when status is "ok" and Gmail
returned no nextPageToken, which proves the complete Inbox membership fitted in
one bounded page. Overflow, malformed data, and provider failures never produce
an inventory that callers may reconcile or use to reset a cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from cuevion_mailbox.gmail_history_delta import GmailRequestWithOneRefresh


_RECOVERY_INBOX_LIMIT = 100
_BOOTSTRAP_PAGE_LIMIT = 10
_MAX_PAGE_TOKEN_BYTES = 4_096
_MAX_PROVIDER_MESSAGE_ID_BYTES = 1_024


@dataclass(frozen=True, slots=True)
class GmailInboxRecoveryInventory:
    status: str
    context: dict
    provider_message_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.status not in {"ok", "overflow", "unavailable", "invalid"}
            or type(self.context) is not dict
            or type(self.provider_message_ids) is not tuple
            or (self.status != "ok" and self.provider_message_ids)
        ):
            raise ValueError("invalid Gmail recovery inventory")


def _valid_provider_message_id(value: object) -> bool:
    if type(value) is not str or not value or not value.isascii():
        return False
    try:
        encoded = value.encode("ascii", errors="strict")
    except UnicodeError:
        return False
    return (
        1 <= len(encoded) <= _MAX_PROVIDER_MESSAGE_ID_BYTES
        and not any(
            character.isspace() or ord(character) < 33
            for character in value
        )
    )


def _valid_page_token(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return 1 <= len(encoded) <= _MAX_PAGE_TOKEN_BYTES


def _inventory_path(page_token: str | None = None) -> str:
    query = {"labelIds": "INBOX", "maxResults": _RECOVERY_INBOX_LIMIT}
    if page_token is not None:
        if not _valid_page_token(page_token):
            raise ValueError("invalid Gmail recovery page token")
        query["pageToken"] = page_token
    return "/messages?" + urlencode(query)


@dataclass(frozen=True, slots=True)
class GmailInboxBootstrapPage:
    status: str
    context: dict
    provider_message_ids: tuple[str, ...]
    next_page_token: str | None


def read_gmail_inbox_bootstrap_page(context: dict, *, page_token: str | None, request_with_one_refresh: GmailRequestWithOneRefresh) -> GmailInboxBootstrapPage:
    query = {"labelIds": "INBOX", "maxResults": _BOOTSTRAP_PAGE_LIMIT}
    if page_token is not None:
        if not _valid_page_token(page_token):
            raise ValueError("invalid Gmail bootstrap page token")
        query["pageToken"] = page_token
    payload, error, next_context, refresh_failure = request_with_one_refresh(context, "/messages?" + urlencode(query))
    current_context = next_context if type(next_context) is dict else context
    if refresh_failure is not None or error is not None or type(payload) is not dict:
        return GmailInboxBootstrapPage("unavailable", current_context, (), None)
    raw_messages = payload.get("messages", [])
    next_token = payload.get("nextPageToken")
    if type(raw_messages) is not list or len(raw_messages) > _BOOTSTRAP_PAGE_LIMIT or (next_token is not None and not _valid_page_token(next_token)):
        return GmailInboxBootstrapPage("invalid", current_context, (), None)
    ids = []
    seen = set()
    for item in raw_messages:
        provider_id = item.get("id") if type(item) is dict else None
        if not _valid_provider_message_id(provider_id) or provider_id in seen:
            return GmailInboxBootstrapPage("invalid", current_context, (), None)
        seen.add(provider_id)
        ids.append(provider_id)
    return GmailInboxBootstrapPage("ok", current_context, tuple(ids), next_token)


def _result(
    status: str,
    context: dict,
    provider_message_ids: tuple[str, ...] = (),
) -> GmailInboxRecoveryInventory:
    return GmailInboxRecoveryInventory(
        status=status,
        context=context,
        provider_message_ids=provider_message_ids,
    )


def read_complete_gmail_inbox_recovery_inventory(
    context: dict,
    *,
    request_with_one_refresh: GmailRequestWithOneRefresh,
) -> GmailInboxRecoveryInventory:
    """Read the complete Inbox membership only when it fits the recovery bound."""

    if type(context) is not dict or not callable(request_with_one_refresh):
        raise ValueError("invalid Gmail recovery inventory request")

    payload, error, next_context, refresh_failure = request_with_one_refresh(
        context,
        _inventory_path(),
    )
    current_context = next_context if type(next_context) is dict else context

    if refresh_failure is not None or error is not None:
        return _result("unavailable", current_context)
    if type(payload) is not dict:
        return _result("invalid", current_context)

    next_page_token = payload.get("nextPageToken")
    if next_page_token is not None:
        if not _valid_page_token(next_page_token):
            return _result("invalid", current_context)
        return _result("overflow", current_context)

    raw_messages = payload.get("messages", [])
    if type(raw_messages) is not list or len(raw_messages) > _RECOVERY_INBOX_LIMIT:
        return _result("invalid", current_context)

    provider_message_ids: list[str] = []
    seen: set[str] = set()
    for raw_message in raw_messages:
        if type(raw_message) is not dict:
            return _result("invalid", current_context)
        provider_message_id = raw_message.get("id")
        if (
            not _valid_provider_message_id(provider_message_id)
            or provider_message_id in seen
        ):
            return _result("invalid", current_context)
        seen.add(provider_message_id)
        provider_message_ids.append(provider_message_id)

    return _result(
        "ok",
        current_context,
        tuple(provider_message_ids),
    )


__all__ = (
    "GmailInboxBootstrapPage",
    "GmailInboxRecoveryInventory",
    "read_complete_gmail_inbox_recovery_inventory",
    "read_gmail_inbox_bootstrap_page",
)
