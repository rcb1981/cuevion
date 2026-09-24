"""Bounded Preview-only mailbox read integration for existing routes.

This module never changes provider state or API response authority. It resolves
the server-owned current source generation and, when present, performs one
bounded reader projection query.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, Sequence

from cuevion_mailbox.repository_contract import (
    BackfillState,
    BootstrapState,
    MailboxProvider,
    MailboxReadAuthority,
    MailboxScope,
    MailboxStateSnapshot,
    MessageProjection,
    SyncCursor,
)
from cuevion_mailbox.runtime import build_active_read_mailbox_reader


_MAX_ROUTE_READ_LIMIT = 100


class _ActiveReader(Protocol):
    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        ...

    def read_cursor(
        self,
        scope: MailboxScope,
        scope_key: str,
    ) -> SyncCursor | None:
        ...

    def list_messages(
        self,
        scope: MailboxScope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[MessageProjection]:
        ...


@dataclass(frozen=True, slots=True)
class PreviewActiveReadResult:
    status: str
    projected_count: int


def preview_active_read_enabled(environment: Mapping[str, str]) -> bool:
    return (
        isinstance(environment, Mapping)
        and environment.get("VERCEL_ENV") == "preview"
        and environment.get("CUEVION_MAILBOX_POSTGRES_MODE") == "active_read"
    )


def run_preview_gmail_active_read(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    limit: int,
    reader: _ActiveReader | None = None,
) -> PreviewActiveReadResult:
    if not preview_active_read_enabled(environment):
        raise RuntimeError("preview active mailbox read is disabled")
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("invalid preview mailbox read limit")

    authority = MailboxReadAuthority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=mailbox_account_identity.casefold(),
    )
    repository = (
        build_active_read_mailbox_reader(environment)
        if reader is None
        else reader
    )
    state = repository.resolve_current_state(authority)
    if state is None:
        return PreviewActiveReadResult("no_scope", 0)
    if type(state) is not MailboxStateSnapshot:
        raise ValueError("invalid preview mailbox read state")
    if state.bootstrap_state is not BootstrapState.READY:
        return PreviewActiveReadResult("not_ready", 0)

    scope = state.scope
    cursor = repository.read_cursor(scope, "gmail-account")
    if cursor is None:
        return PreviewActiveReadResult("cursor_missing", 0)
    if (
        type(cursor) is not SyncCursor
        or cursor.provider is not MailboxProvider.GOOGLE
        or cursor.scope_key != "gmail-account"
        or cursor.gmail_history_id is None
        or cursor.backfill_state is not BackfillState.COMPLETE
        or cursor.backfill_cursor is not None
    ):
        return PreviewActiveReadResult("not_ready", 0)

    projections = repository.list_messages(
        scope,
        limit=min(limit, _MAX_ROUTE_READ_LIMIT),
    )
    return PreviewActiveReadResult("resolved", len(projections))


__all__ = (
    "PreviewActiveReadResult",
    "preview_active_read_enabled",
    "run_preview_gmail_active_read",
)
