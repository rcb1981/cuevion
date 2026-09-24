"""Preview-only durable Gmail cache authority for existing routes.

This module never changes provider state. User-visible durable Gmail Inbox
membership is permitted only after complete durable readiness has already been
proven and the provider account history cursor matches exactly.
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
_GMAIL_SCOPE_KEY = "gmail-account"


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


@dataclass(frozen=True, slots=True)
class PreviewGmailAuthoritativeReadPlan:
    status: str
    provider_message_ids: tuple[str, ...]
    history_id: str | None
    source_generation: int | None

    def __post_init__(self) -> None:
        if self.status not in {"cache_authoritative", "provider_required"}:
            raise ValueError("invalid Preview Gmail authoritative read plan")
        if type(self.provider_message_ids) is not tuple or any(
            type(value) is not str or not value for value in self.provider_message_ids
        ):
            raise ValueError("invalid Preview Gmail authoritative read plan")
        if len(set(self.provider_message_ids)) != len(self.provider_message_ids):
            raise ValueError("invalid Preview Gmail authoritative read plan")
        if self.status == "cache_authoritative":
            if (
                type(self.history_id) is not str
                or not self.history_id
                or type(self.source_generation) is not int
                or self.source_generation < 1
            ):
                raise ValueError("invalid Preview Gmail authoritative read plan")
        elif (
            self.provider_message_ids
            or self.history_id is not None
            or self.source_generation is not None
        ):
            raise ValueError("invalid Preview Gmail authoritative read plan")


def preview_active_read_enabled(environment: Mapping[str, str]) -> bool:
    return (
        isinstance(environment, Mapping)
        and environment.get("VERCEL_ENV") == "preview"
        and environment.get("CUEVION_MAILBOX_POSTGRES_MODE") == "active_read"
    )


def _authority(
    *,
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
) -> MailboxReadAuthority:
    return MailboxReadAuthority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=mailbox_account_identity.casefold(),
    )


def _reader(environment: Mapping[str, str], reader: _ActiveReader | None):
    return build_active_read_mailbox_reader(environment) if reader is None else reader


def _resolve_complete_ready(
    repository: _ActiveReader,
    authority: MailboxReadAuthority,
) -> tuple[
    str,
    MailboxStateSnapshot | None,
    SyncCursor | None,
]:
    state = repository.resolve_current_state(authority)
    if state is None:
        return "no_scope", None, None
    if type(state) is not MailboxStateSnapshot:
        raise ValueError("invalid preview mailbox read state")
    if state.bootstrap_state is not BootstrapState.READY:
        return "not_ready", state, None

    cursor = repository.read_cursor(state.scope, _GMAIL_SCOPE_KEY)
    if cursor is None:
        return "not_ready", state, None
    if type(cursor) is not SyncCursor:
        raise ValueError("invalid preview mailbox read cursor")
    if (
        cursor.provider is not MailboxProvider.GOOGLE
        or cursor.scope_key != _GMAIL_SCOPE_KEY
        or cursor.gmail_history_id is None
        or cursor.backfill_state is not BackfillState.COMPLETE
        or cursor.backfill_cursor is not None
    ):
        return "not_ready", state, cursor
    return "ready", state, cursor


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

    authority = _authority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=mailbox_account_identity,
    )
    repository = _reader(environment, reader)
    readiness, state, _cursor = _resolve_complete_ready(
        repository,
        authority,
    )
    if readiness != "ready":
        return PreviewActiveReadResult(readiness, 0)
    if state is None:
        raise RuntimeError("invalid preview mailbox read readiness")

    projections = repository.list_messages(
        state.scope,
        limit=min(limit, _MAX_ROUTE_READ_LIMIT),
    )
    return PreviewActiveReadResult("resolved", len(projections))


def plan_preview_gmail_authoritative_read(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    provider_history_id: str,
    limit: int,
    reader: _ActiveReader | None = None,
) -> PreviewGmailAuthoritativeReadPlan:
    """Return durable Gmail membership only after complete readiness + freshness."""

    if not preview_active_read_enabled(environment):
        raise RuntimeError("preview active mailbox read is disabled")
    if (
        type(limit) is not int
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAX_ROUTE_READ_LIMIT
        or type(provider_history_id) is not str
        or not 1 <= len(provider_history_id) <= 128
        or not provider_history_id.isascii()
        or not provider_history_id.isdigit()
    ):
        raise ValueError("invalid Preview Gmail authoritative read request")

    authority = _authority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=mailbox_account_identity,
    )
    repository = _reader(environment, reader)
    readiness, state, cursor = _resolve_complete_ready(
        repository,
        authority,
    )
    if readiness != "ready":
        return PreviewGmailAuthoritativeReadPlan(
            "provider_required",
            (),
            None,
            None,
        )
    if state is None or cursor is None:
        raise RuntimeError("invalid Preview Gmail authoritative readiness")
    if cursor.gmail_history_id != provider_history_id:
        return PreviewGmailAuthoritativeReadPlan(
            "provider_required",
            (),
            None,
            None,
        )

    projections = tuple(
        repository.list_messages(
            state.scope,
            limit=limit,
        )
    )
    if len(projections) > limit:
        raise RuntimeError("invalid Preview Gmail authoritative read result")

    provider_ids: list[str] = []
    for projection in projections:
        if type(projection) is not MessageProjection:
            raise RuntimeError("invalid Preview Gmail authoritative read result")
        projection.validate_for(MailboxProvider.GOOGLE)
        identity = projection.identity
        if (
            projection.provider_deleted
            or identity.provider_folder.casefold() != "inbox"
            or type(identity.provider_message_id) is not str
            or not identity.provider_message_id
        ):
            raise RuntimeError("invalid Preview Gmail authoritative read result")
        provider_ids.append(identity.provider_message_id)

    if len(set(provider_ids)) != len(provider_ids):
        raise RuntimeError("invalid Preview Gmail authoritative read result")

    return PreviewGmailAuthoritativeReadPlan(
        "cache_authoritative",
        tuple(provider_ids),
        provider_history_id,
        state.scope.source_generation,
    )


__all__ = (
    "PreviewActiveReadResult",
    "PreviewGmailAuthoritativeReadPlan",
    "plan_preview_gmail_authoritative_read",
    "preview_active_read_enabled",
    "run_preview_gmail_active_read",
)
