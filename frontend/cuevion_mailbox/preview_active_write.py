"""Preview-only orchestration for durable Gmail metadata writes.

This helper keeps the provider response authoritative. Callers decide whether a
durable write failure is user-visible; the first route integration only emits
fixed Preview diagnostics and continues returning the provider snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable, Protocol

from cuevion_mailbox.gmail_delta_plan import (
    build_gmail_delta_commit,
    build_gmail_history_delta_commit,
)
from cuevion_mailbox.gmail_history_delta import (
    GmailRequestWithOneRefresh,
    read_gmail_history_delta,
)
from cuevion_mailbox.gmail_projection import project_gmail_snapshot
from cuevion_mailbox.repository_contract import (
    BackfillState,
    BootstrapState,
    CurrentStateInitializationOutcome,
    CurrentStateInitializationResult,
    DeltaCommitOutcome,
    MailboxProvider,
    MailboxReadAuthority,
    MailboxStateSnapshot,
    MessageProjection,
    SyncCursor,
)
from cuevion_mailbox.runtime import (
    ActiveWriteMailboxRepositories,
    build_active_write_mailbox_repositories,
)


_MAX_ROUTE_WRITE_LIMIT = 100
_GMAIL_SCOPE_KEY = "gmail-account"


class _Reader(Protocol):
    def resolve_current_state(
        self,
        authority: MailboxReadAuthority,
    ) -> MailboxStateSnapshot | None:
        ...

    def read_cursor(self, scope, scope_key: str) -> SyncCursor | None:
        ...

    def read_messages_by_provider_message_ids(
        self,
        scope,
        provider_message_ids: Sequence[str],
    ) -> Sequence[MessageProjection]:
        ...

    def list_messages(
        self,
        scope,
        *,
        limit: int,
        before_timestamp_millis: int | None = None,
    ) -> Sequence[MessageProjection]:
        ...


class _Writer(Protocol):
    def initialize_current_state(
        self,
        authority: MailboxReadAuthority,
        *,
        initialized_at_millis: int,
    ) -> CurrentStateInitializationResult:
        ...

    def commit_provider_delta(self, commit) -> DeltaCommitOutcome:
        ...


class _Repositories(Protocol):
    reader: _Reader
    writer: _Writer


@dataclass(frozen=True, slots=True)
class PreviewDurableWriteResult:
    status: str
    mutation_count: int
    source_generation: int | None


@dataclass(frozen=True, slots=True)
class PreviewGmailHistoryRecovery:
    status: str
    context: dict
    preview: dict | None = None
    candidate_source: dict | None = None

    def __post_init__(self) -> None:
        if (
            self.status not in {"recovered", "terminal_absent", "retry"}
            or type(self.context) is not dict
        ):
            raise ValueError("invalid Preview Gmail History recovery")
        if self.status == "recovered":
            if type(self.preview) is not dict or type(self.candidate_source) is not dict:
                raise ValueError("invalid Preview Gmail History recovery")
        elif self.preview is not None or self.candidate_source is not None:
            raise ValueError("invalid Preview Gmail History recovery")


PreviewGmailHistoryRecoveryCallable = Callable[
    [dict, str],
    PreviewGmailHistoryRecovery,
]


@dataclass(frozen=True, slots=True)
class PreviewGmailHistorySyncResult:
    status: str
    context: dict
    mutation_count: int
    source_generation: int | None
    next_history_id: str | None
    affected_count: int


def preview_active_write_enabled(environment: Mapping[str, str]) -> bool:
    return (
        isinstance(environment, Mapping)
        and environment.get("VERCEL_ENV") == "preview"
        and environment.get("CUEVION_MAILBOX_POSTGRES_MODE") == "active_write"
    )


def _next_cursor(
    current: SyncCursor | None,
    *,
    gmail_history_id: str,
) -> SyncCursor:
    if (
        type(gmail_history_id) is not str
        or not 1 <= len(gmail_history_id) <= 128
        or not gmail_history_id.isascii()
        or not gmail_history_id.isdigit()
    ):
        raise ValueError("invalid Gmail durable write history id")

    if current is None:
        return SyncCursor(
            scope_key=_GMAIL_SCOPE_KEY,
            cursor_generation=1,
            provider=MailboxProvider.GOOGLE,
            gmail_history_id=gmail_history_id,
            imap_uid_validity=None,
            imap_highest_uid=None,
            imap_uidnext_observed=None,
            backfill_state=BackfillState.NOT_STARTED,
            backfill_cursor=None,
            row_version=1,
        )

    if (
        type(current) is not SyncCursor
        or current.provider is not MailboxProvider.GOOGLE
        or current.scope_key != _GMAIL_SCOPE_KEY
    ):
        raise ValueError("invalid Gmail durable write cursor")

    return SyncCursor(
        scope_key=current.scope_key,
        cursor_generation=current.cursor_generation,
        provider=current.provider,
        gmail_history_id=gmail_history_id,
        imap_uid_validity=None,
        imap_highest_uid=None,
        imap_uidnext_observed=None,
        backfill_state=current.backfill_state,
        backfill_cursor=current.backfill_cursor,
        row_version=current.row_version + 1,
    )


def run_preview_gmail_history_sync(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    context: dict,
    request_with_one_refresh: GmailRequestWithOneRefresh,
    recover_exact_message: PreviewGmailHistoryRecoveryCallable,
    committed_at_millis: int,
    repositories: _Repositories | None = None,
) -> PreviewGmailHistorySyncResult:
    """Apply one complete bounded Gmail History window when a cursor exists.

    No state is created here. Missing state/cursor returns bootstrap_required so
    the caller can retain the existing profile-before-snapshot bootstrap flow.
    Any incomplete History/provider recovery returns without advancing cursor.
    """

    if not preview_active_write_enabled(environment):
        raise RuntimeError("preview active mailbox write is disabled")
    if (
        type(context) is not dict
        or not callable(request_with_one_refresh)
        or not callable(recover_exact_message)
        or type(committed_at_millis) is not int
        or isinstance(committed_at_millis, bool)
        or committed_at_millis < 0
    ):
        raise ValueError("invalid Preview Gmail History sync")

    authority = MailboxReadAuthority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=mailbox_account_identity.casefold(),
    )
    runtime_repositories = (
        build_active_write_mailbox_repositories(environment)
        if repositories is None
        else repositories
    )

    state = runtime_repositories.reader.resolve_current_state(authority)
    if state is None:
        return PreviewGmailHistorySyncResult(
            "bootstrap_required",
            context,
            0,
            None,
            None,
            0,
        )
    if type(state) is not MailboxStateSnapshot:
        raise ValueError("invalid Preview Gmail History state")

    scope = state.scope
    current_cursor = runtime_repositories.reader.read_cursor(
        scope,
        _GMAIL_SCOPE_KEY,
    )
    if current_cursor is None:
        return PreviewGmailHistorySyncResult(
            "bootstrap_required",
            context,
            0,
            scope.source_generation,
            None,
            0,
        )
    if (
        type(current_cursor) is not SyncCursor
        or current_cursor.provider is not MailboxProvider.GOOGLE
        or current_cursor.scope_key != _GMAIL_SCOPE_KEY
        or current_cursor.gmail_history_id is None
    ):
        raise ValueError("invalid Preview Gmail History cursor")

    delta = read_gmail_history_delta(
        context,
        start_history_id=current_cursor.gmail_history_id,
        request_with_one_refresh=request_with_one_refresh,
    )
    current_context = delta.context
    if delta.status != "ok" or delta.next_history_id is None:
        return PreviewGmailHistorySyncResult(
            delta.status,
            current_context,
            0,
            scope.source_generation,
            None,
            len(delta.affected_message_ids),
        )

    recovered_previews: list[object] = []
    recovered_sources: list[object] = []
    verified_absent: list[str] = []
    for provider_message_id in delta.affected_message_ids:
        recovered = recover_exact_message(
            current_context,
            provider_message_id,
        )
        if type(recovered) is not PreviewGmailHistoryRecovery:
            raise ValueError("invalid Preview Gmail History recovery")
        current_context = recovered.context
        if recovered.status == "retry":
            return PreviewGmailHistorySyncResult(
                "recovery_unavailable",
                current_context,
                0,
                scope.source_generation,
                None,
                len(delta.affected_message_ids),
            )
        if recovered.status == "terminal_absent":
            verified_absent.append(provider_message_id)
            continue
        recovered_previews.append(recovered.preview)
        recovered_sources.append(recovered.candidate_source)

    current_projections = tuple(
        runtime_repositories.reader.read_messages_by_provider_message_ids(
            scope,
            list(delta.affected_message_ids),
        )
    )
    records = project_gmail_snapshot(
        scope,
        recovered_previews,
        recovered_sources,
    )
    next_cursor = _next_cursor(
        current_cursor,
        gmail_history_id=delta.next_history_id,
    )
    commit = build_gmail_history_delta_commit(
        scope,
        records,
        verified_absent,
        list(current_projections),
        expected_state_row_version=state.row_version,
        current_cursor=current_cursor,
        next_cursor=next_cursor,
        committed_at_millis=committed_at_millis,
        next_bootstrap_state=BootstrapState.RECENT_READY,
    )

    if (
        current_cursor.gmail_history_id == delta.next_history_id
        and not commit.mutations
    ):
        return PreviewGmailHistorySyncResult(
            "unchanged",
            current_context,
            0,
            scope.source_generation,
            delta.next_history_id,
            len(delta.affected_message_ids),
        )

    outcome = runtime_repositories.writer.commit_provider_delta(commit)
    return PreviewGmailHistorySyncResult(
        outcome.value,
        current_context,
        len(commit.mutations),
        scope.source_generation,
        delta.next_history_id,
        len(delta.affected_message_ids),
    )


def run_preview_gmail_durable_write(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    previews: list[object] | tuple[object, ...],
    candidate_sources: list[object] | tuple[object, ...],
    gmail_history_id: str,
    committed_at_millis: int,
    repositories: _Repositories | None = None,
) -> PreviewDurableWriteResult:
    if not preview_active_write_enabled(environment):
        raise RuntimeError("preview active mailbox write is disabled")
    if (
        type(committed_at_millis) is not int
        or isinstance(committed_at_millis, bool)
        or committed_at_millis < 0
        or type(previews) not in (list, tuple)
        or type(candidate_sources) not in (list, tuple)
        or len(previews) > _MAX_ROUTE_WRITE_LIMIT
        or len(candidate_sources) > _MAX_ROUTE_WRITE_LIMIT
    ):
        raise ValueError("invalid Preview Gmail durable write")

    authority = MailboxReadAuthority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=mailbox_account_identity.casefold(),
    )
    runtime_repositories = (
        build_active_write_mailbox_repositories(environment)
        if repositories is None
        else repositories
    )

    initialized = runtime_repositories.writer.initialize_current_state(
        authority,
        initialized_at_millis=committed_at_millis,
    )
    if (
        type(initialized) is not CurrentStateInitializationResult
        or initialized.outcome is CurrentStateInitializationOutcome.CONFLICT
        or type(initialized.state) is not MailboxStateSnapshot
    ):
        return PreviewDurableWriteResult("state_conflict", 0, None)

    state = initialized.state
    scope = state.scope
    current_cursor = runtime_repositories.reader.read_cursor(
        scope,
        _GMAIL_SCOPE_KEY,
    )
    current_projections = tuple(
        runtime_repositories.reader.list_messages(
            scope,
            limit=_MAX_ROUTE_WRITE_LIMIT,
        )
    )
    records = project_gmail_snapshot(
        scope,
        previews,
        candidate_sources,
    )
    next_cursor = _next_cursor(
        current_cursor,
        gmail_history_id=gmail_history_id,
    )
    commit = build_gmail_delta_commit(
        scope,
        records,
        list(current_projections),
        expected_state_row_version=state.row_version,
        current_cursor=current_cursor,
        next_cursor=next_cursor,
        committed_at_millis=committed_at_millis,
        next_bootstrap_state=BootstrapState.RECENT_READY,
    )

    if (
        current_cursor is not None
        and current_cursor.gmail_history_id == gmail_history_id
        and not commit.mutations
    ):
        return PreviewDurableWriteResult(
            "unchanged",
            0,
            scope.source_generation,
        )

    outcome = runtime_repositories.writer.commit_provider_delta(commit)
    return PreviewDurableWriteResult(
        outcome.value,
        len(commit.mutations),
        scope.source_generation,
    )


__all__ = (
    "PreviewDurableWriteResult",
    "PreviewGmailHistoryRecovery",
    "PreviewGmailHistorySyncResult",
    "preview_active_write_enabled",
    "run_preview_gmail_durable_write",
    "run_preview_gmail_history_sync",
)
