"""Account-level Gmail history cursor retrieval through an injected request boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


GmailRequestWithOneRefresh = Callable[
    [dict, str],
    tuple[dict | None, dict | None, dict, dict | None],
]


@dataclass(frozen=True, slots=True)
class GmailAccountHistoryResult:
    status: str
    context: dict
    history_id: str | None


def read_gmail_account_history(
    context: dict,
    *,
    request_with_one_refresh: GmailRequestWithOneRefresh,
) -> GmailAccountHistoryResult:
    if type(context) is not dict or not callable(request_with_one_refresh):
        raise ValueError("invalid Gmail account history request")

    payload, error, next_context, refresh_failure = request_with_one_refresh(
        context,
        "/profile",
    )
    if type(next_context) is not dict:
        next_context = context

    if refresh_failure is not None or error is not None:
        return GmailAccountHistoryResult(
            "unavailable",
            next_context,
            None,
        )
    if type(payload) is not dict:
        return GmailAccountHistoryResult(
            "invalid",
            next_context,
            None,
        )

    expected_email = context.get("mailbox_email")
    actual_email = payload.get("emailAddress")
    history_id = payload.get("historyId")
    if (
        type(expected_email) is not str
        or not expected_email
        or type(actual_email) is not str
        or actual_email.casefold() != expected_email.casefold()
        or type(history_id) is not str
        or not 1 <= len(history_id) <= 128
        or not history_id.isascii()
        or not history_id.isdigit()
    ):
        return GmailAccountHistoryResult(
            "invalid",
            next_context,
            None,
        )

    return GmailAccountHistoryResult(
        "ok",
        next_context,
        history_id,
    )


__all__ = (
    "GmailAccountHistoryResult",
    "read_gmail_account_history",
)
