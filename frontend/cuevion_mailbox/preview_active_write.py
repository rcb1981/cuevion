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
    build_gmail_stale_recovery_commit,
)
from cuevion_mailbox.gmail_history_delta import (
    GmailRequestWithOneRefresh,
    read_gmail_history_delta,
)
from cuevion_mailbox.gmail_projection import project_gmail_snapshot
from cuevion_mailbox.gmail_recovery_inventory import (
    read_complete_gmail_inbox_recovery_inventory,
    read_gmail_inbox_bootstrap_page,
)
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
    build_production_bootstrap_mailbox_repositories,
    production_bootstrap_authority_enabled,
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

    def read_active_message_inventory(
        self,
        scope,
        *,
        limit: int,
    ):
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


@dataclass(frozen=True, slots=True)
class PreviewGmailStaleRecoveryResult:
    status: str
    context: dict
    mutation_count: int
    source_generation: int | None
    next_history_id: str | None
    provider_count: int


def preview_active_write_enabled(environment: Mapping[str, str]) -> bool:
    return (
        isinstance(environment, Mapping)
        and environment.get("VERCEL_ENV") == "preview"
        and environment.get("CUEVION_MAILBOX_POSTGRES_MODE") == "active_write"
    )


def gmail_durable_write_enabled(environment: Mapping[str, str]) -> bool:
    return preview_active_write_enabled(environment) or production_bootstrap_authority_enabled(environment)


def _runtime_repositories(environment: Mapping[str, str]):
    if preview_active_write_enabled(environment):
        return build_active_write_mailbox_repositories(environment)
    if production_bootstrap_authority_enabled(environment):
        return build_production_bootstrap_mailbox_repositories(environment)
    raise RuntimeError("Gmail durable mailbox write is disabled")


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


def _next_complete_cursor(
    current: SyncCursor,
    *,
    gmail_history_id: str,
) -> SyncCursor:
    next_cursor = _next_cursor(
        current,
        gmail_history_id=gmail_history_id,
    )
    return SyncCursor(
        scope_key=next_cursor.scope_key,
        cursor_generation=next_cursor.cursor_generation,
        provider=next_cursor.provider,
        gmail_history_id=next_cursor.gmail_history_id,
        imap_uid_validity=next_cursor.imap_uid_validity,
        imap_highest_uid=next_cursor.imap_highest_uid,
        imap_uidnext_observed=next_cursor.imap_uidnext_observed,
        backfill_state=BackfillState.COMPLETE,
        backfill_cursor=None,
        row_version=next_cursor.row_version,
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

    if not gmail_durable_write_enabled(environment):
        raise RuntimeError("Gmail durable mailbox write is disabled")
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
        _runtime_repositories(environment)
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
    if production_bootstrap_authority_enabled(environment) and (
        state.bootstrap_state is not BootstrapState.READY
        or current_cursor.backfill_state is not BackfillState.COMPLETE
        or current_cursor.backfill_cursor is not None
    ):
        return PreviewGmailHistorySyncResult(
            "full_sync_required",
            context,
            0,
            scope.source_generation,
            None,
            0,
        )
    if state.bootstrap_state not in {
        BootstrapState.RECENT_READY,
        BootstrapState.BACKFILLING,
        BootstrapState.READY,
    }:
        return PreviewGmailHistorySyncResult(
            "state_not_ready",
            context,
            0,
            scope.source_generation,
            None,
            0,
        )

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
        next_bootstrap_state=state.bootstrap_state,
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


def run_production_gmail_bootstrap_page(*, environment, workspace_id, owner_user_id, mailbox_id, mailbox_account_identity, context, request_with_one_refresh, recover_exact_message, committed_at_millis, repositories=None):
    if not production_bootstrap_authority_enabled(environment):
        raise RuntimeError("Production Gmail bootstrap page is disabled")
    authority = MailboxReadAuthority(workspace_id=workspace_id, owner_user_id=owner_user_id, mailbox_id=mailbox_id, provider=MailboxProvider.GOOGLE, provider_account_identity=mailbox_account_identity.casefold())
    repos = _runtime_repositories(environment) if repositories is None else repositories
    state = repos.reader.resolve_current_state(authority)
    if type(state) is not MailboxStateSnapshot or state.bootstrap_state not in {BootstrapState.RECENT_READY, BootstrapState.BACKFILLING}:
        return PreviewGmailStaleRecoveryResult("state_not_ready", context, 0, None if state is None else state.scope.source_generation, None, 0)
    cursor = repos.reader.read_cursor(state.scope, _GMAIL_SCOPE_KEY)
    if type(cursor) is not SyncCursor or cursor.backfill_state not in {BackfillState.NOT_STARTED, BackfillState.RUNNING}:
        return PreviewGmailStaleRecoveryResult("cursor_not_backfillable", context, 0, state.scope.source_generation, None, 0)
    page = read_gmail_inbox_bootstrap_page(context, page_token=cursor.backfill_cursor, request_with_one_refresh=request_with_one_refresh)
    if page.status != "ok":
        return PreviewGmailStaleRecoveryResult("provider_" + page.status, page.context, 0, state.scope.source_generation, None, 0)
    current_context = page.context
    previews, sources, ids = [], [], []
    for provider_id in page.provider_message_ids:
        recovered = recover_exact_message(current_context, provider_id)
        if type(recovered) is not PreviewGmailHistoryRecovery:
            raise ValueError("invalid Production Gmail bootstrap recovery")
        current_context = recovered.context
        if recovered.status == "retry":
            return PreviewGmailStaleRecoveryResult("recovery_unavailable", current_context, 0, state.scope.source_generation, None, len(page.provider_message_ids))
        if recovered.status == "recovered":
            ids.append(provider_id); previews.append(recovered.preview); sources.append(recovered.candidate_source)
    current = tuple(repos.reader.read_messages_by_provider_message_ids(state.scope, ids))
    records = project_gmail_snapshot(state.scope, previews, sources)
    base = _next_cursor(cursor, gmail_history_id=cursor.gmail_history_id)
    complete = page.next_page_token is None
    next_cursor = SyncCursor(scope_key=base.scope_key, cursor_generation=base.cursor_generation, provider=base.provider, gmail_history_id=base.gmail_history_id, imap_uid_validity=None, imap_highest_uid=None, imap_uidnext_observed=None, backfill_state=BackfillState.COMPLETE if complete else BackfillState.RUNNING, backfill_cursor=None if complete else page.next_page_token, row_version=base.row_version)
    commit = build_gmail_delta_commit(state.scope, records, list(current), expected_state_row_version=state.row_version, current_cursor=cursor, next_cursor=next_cursor, committed_at_millis=committed_at_millis, next_bootstrap_state=BootstrapState.READY if complete else BootstrapState.BACKFILLING)
    outcome = repos.writer.commit_provider_delta(commit)
    return PreviewGmailStaleRecoveryResult(outcome.value, current_context, len(commit.mutations), state.scope.source_generation, cursor.gmail_history_id if complete else None, len(page.provider_message_ids))


def run_preview_gmail_stale_recovery(
    *,
    environment: Mapping[str, str],
    workspace_id: str,
    owner_user_id: str,
    mailbox_id: str,
    mailbox_account_identity: str,
    context: dict,
    fresh_history_id: str,
    request_with_one_refresh: GmailRequestWithOneRefresh,
    recover_exact_message: PreviewGmailHistoryRecoveryCallable,
    committed_at_millis: int,
    repositories: _Repositories | None = None,
) -> PreviewGmailStaleRecoveryResult:
    """Reconcile a complete bounded Inbox before replacing a stale Gmail cursor."""

    if not gmail_durable_write_enabled(environment):
        raise RuntimeError("Gmail durable mailbox write is disabled")
    if (
        type(context) is not dict
        or type(fresh_history_id) is not str
        or not 1 <= len(fresh_history_id) <= 128
        or not fresh_history_id.isascii()
        or not fresh_history_id.isdigit()
        or not callable(request_with_one_refresh)
        or not callable(recover_exact_message)
        or type(committed_at_millis) is not int
        or isinstance(committed_at_millis, bool)
        or committed_at_millis < 0
    ):
        raise ValueError("invalid Preview Gmail stale recovery")

    authority = MailboxReadAuthority(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        mailbox_id=mailbox_id,
        provider=MailboxProvider.GOOGLE,
        provider_account_identity=mailbox_account_identity.casefold(),
    )
    runtime_repositories = (
        _runtime_repositories(environment)
        if repositories is None
        else repositories
    )

    state = runtime_repositories.reader.resolve_current_state(authority)
    if state is None:
        return PreviewGmailStaleRecoveryResult(
            "state_missing",
            context,
            0,
            None,
            None,
            0,
        )
    if type(state) is not MailboxStateSnapshot:
        raise ValueError("invalid Preview Gmail stale recovery state")
    if state.bootstrap_state not in {
        BootstrapState.RECENT_READY,
        BootstrapState.BACKFILLING,
        BootstrapState.READY,
    }:
        return PreviewGmailStaleRecoveryResult(
            "state_not_ready",
            context,
            0,
            state.scope.source_generation,
            None,
            0,
        )

    scope = state.scope
    current_cursor = runtime_repositories.reader.read_cursor(
        scope,
        _GMAIL_SCOPE_KEY,
    )
    if current_cursor is None:
        return PreviewGmailStaleRecoveryResult(
            "cursor_missing",
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
        raise ValueError("invalid Preview Gmail stale recovery cursor")

    inventory = read_complete_gmail_inbox_recovery_inventory(
        context,
        request_with_one_refresh=request_with_one_refresh,
    )
    current_context = inventory.context
    if inventory.status != "ok":
        return PreviewGmailStaleRecoveryResult(
            "provider_" + inventory.status,
            current_context,
            0,
            scope.source_generation,
            None,
            0,
        )

    active_inventory = runtime_repositories.reader.read_active_message_inventory(
        scope,
        limit=_MAX_ROUTE_WRITE_LIMIT,
    )
    if active_inventory.overflow:
        return PreviewGmailStaleRecoveryResult(
            "durable_overflow",
            current_context,
            0,
            scope.source_generation,
            None,
            len(inventory.provider_message_ids),
        )

    recovered_previews: list[object] = []
    recovered_sources: list[object] = []
    recovered_provider_ids: list[str] = []
    terminal_absent: list[str] = []
    for provider_message_id in inventory.provider_message_ids:
        recovered = recover_exact_message(
            current_context,
            provider_message_id,
        )
        if type(recovered) is not PreviewGmailHistoryRecovery:
            raise ValueError("invalid Preview Gmail stale recovery")
        current_context = recovered.context
        if recovered.status == "retry":
            return PreviewGmailStaleRecoveryResult(
                "recovery_unavailable",
                current_context,
                0,
                scope.source_generation,
                None,
                len(inventory.provider_message_ids),
            )
        if recovered.status == "terminal_absent":
            terminal_absent.append(provider_message_id)
            continue
        recovered_provider_ids.append(provider_message_id)
        recovered_previews.append(recovered.preview)
        recovered_sources.append(recovered.candidate_source)

    exact_current = tuple(
        runtime_repositories.reader.read_messages_by_provider_message_ids(
            scope,
            recovered_provider_ids,
        )
    )
    records = project_gmail_snapshot(
        scope,
        recovered_previews,
        recovered_sources,
    )
    next_cursor = _next_complete_cursor(
        current_cursor,
        gmail_history_id=fresh_history_id,
    )
    commit = build_gmail_stale_recovery_commit(
        scope,
        list(inventory.provider_message_ids),
        records,
        terminal_absent,
        list(active_inventory.projections),
        list(exact_current),
        expected_state_row_version=state.row_version,
        current_cursor=current_cursor,
        next_cursor=next_cursor,
        committed_at_millis=committed_at_millis,
        next_bootstrap_state=BootstrapState.READY,
    )

    if (
        current_cursor.gmail_history_id == fresh_history_id
        and current_cursor.backfill_state is BackfillState.COMPLETE
        and current_cursor.backfill_cursor is None
        and state.bootstrap_state is BootstrapState.READY
        and not commit.mutations
    ):
        return PreviewGmailStaleRecoveryResult(
            "unchanged",
            current_context,
            0,
            scope.source_generation,
            fresh_history_id,
            len(inventory.provider_message_ids),
        )

    outcome = runtime_repositories.writer.commit_provider_delta(commit)
    return PreviewGmailStaleRecoveryResult(
        outcome.value,
        current_context,
        len(commit.mutations),
        scope.source_generation,
        fresh_history_id,
        len(inventory.provider_message_ids),
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
    if not gmail_durable_write_enabled(environment):
        raise RuntimeError("Gmail durable mailbox write is disabled")
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
        _runtime_repositories(environment)
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
    "gmail_durable_write_enabled",
    "preview_active_write_enabled",
    "run_production_gmail_bootstrap_page",
    "run_preview_gmail_durable_write",
    "run_preview_gmail_history_sync",
)
