"""Provider-neutral contracts for the inactive durable mailbox repository.

The contracts define data and atomicity boundaries only. They do not open
connections, read environment variables, call providers, enqueue jobs, or
activate routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class MailboxProvider(str, Enum):
    GOOGLE = "google"
    CUSTOM_IMAP = "custom_imap"


class BootstrapState(str, Enum):
    NOT_STARTED = "not_started"
    RECENT_SYNC = "recent_sync"
    RECENT_READY = "recent_ready"
    BACKFILLING = "backfilling"
    READY = "ready"
    RECOVERING = "recovering"
    BLOCKED = "blocked"


class BackfillState(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETE = "complete"
    RECOVERING = "recovering"
    BLOCKED = "blocked"


class BodyState(str, Enum):
    NOT_CACHED = "not_cached"
    CACHED = "cached"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class MessageMutationKind(str, Enum):
    UPSERT = "upsert"
    TOMBSTONE = "tombstone"


class OutboxEventType(str, Enum):
    MESSAGE_ADDED = "message_added"
    MESSAGE_CHANGED = "message_changed"
    MESSAGE_DELETED = "message_deleted"


class DeltaCommitOutcome(str, Enum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    STALE_GENERATION = "stale_generation"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class MailboxScope:
    workspace_id: str
    owner_user_id: str
    mailbox_id: str
    source_generation: int
    provider: MailboxProvider
    provider_account_identity: str

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not str or not value
                for value in (
                    self.workspace_id,
                    self.owner_user_id,
                    self.mailbox_id,
                    self.provider_account_identity,
                )
            )
            or type(self.source_generation) is not int
            or self.source_generation < 1
            or self.provider_account_identity
            != self.provider_account_identity.casefold()
        ):
            raise ValueError("invalid mailbox scope")


@dataclass(frozen=True, slots=True)
class SyncCursor:
    scope_key: str
    cursor_generation: int
    provider: MailboxProvider
    gmail_history_id: str | None
    imap_uid_validity: str | None
    imap_highest_uid: int | None
    imap_uidnext_observed: int | None
    backfill_state: BackfillState
    backfill_cursor: str | None
    row_version: int

    def __post_init__(self) -> None:
        google_shape = (
            self.provider is MailboxProvider.GOOGLE
            and self.scope_key == "gmail-account"
            and isinstance(self.gmail_history_id, str)
            and self.gmail_history_id.isdigit()
            and self.imap_uid_validity is None
            and self.imap_highest_uid is None
            and self.imap_uidnext_observed is None
        )
        imap_shape = (
            self.provider is MailboxProvider.CUSTOM_IMAP
            and type(self.scope_key) is str
            and bool(self.scope_key)
            and self.gmail_history_id is None
            and isinstance(self.imap_uid_validity, str)
            and self.imap_uid_validity.isdigit()
            and not self.imap_uid_validity.startswith("0")
            and type(self.imap_highest_uid) is int
            and 1 <= self.imap_highest_uid <= 4_294_967_295
            and (
                self.imap_uidnext_observed is None
                or (
                    type(self.imap_uidnext_observed) is int
                    and 1 <= self.imap_uidnext_observed <= 4_294_967_296
                )
            )
        )
        if (
            not (google_shape or imap_shape)
            or type(self.cursor_generation) is not int
            or self.cursor_generation < 1
            or type(self.row_version) is not int
            or self.row_version < 1
            or (
                self.backfill_cursor is not None
                and (
                    type(self.backfill_cursor) is not str
                    or not self.backfill_cursor
                )
            )
        ):
            raise ValueError("invalid sync cursor")


@dataclass(frozen=True, slots=True)
class MessageIdentity:
    message_id: str
    provider_message_id: str | None
    provider_folder: str
    imap_uid_validity: str | None
    imap_uid: int | None

    def validate_for(self, provider: MailboxProvider) -> None:
        if (
            type(self.message_id) is not str
            or not self.message_id
            or type(self.provider_folder) is not str
            or not self.provider_folder
        ):
            raise ValueError("invalid message identity")

        if provider is MailboxProvider.GOOGLE:
            if (
                type(self.provider_message_id) is not str
                or not self.provider_message_id
                or self.imap_uid_validity is not None
                or self.imap_uid is not None
            ):
                raise ValueError("invalid Gmail message identity")
            return

        if (
            self.provider_message_id is not None
            or type(self.imap_uid_validity) is not str
            or not self.imap_uid_validity.isdigit()
            or self.imap_uid_validity.startswith("0")
            or type(self.imap_uid) is not int
            or not 1 <= self.imap_uid <= 4_294_967_295
        ):
            raise ValueError("invalid IMAP message identity")


@dataclass(frozen=True, slots=True)
class MessageProjection:
    identity: MessageIdentity
    provider_thread_id: str | None
    metadata_hash: str
    body_state: BodyState
    unread: bool
    starred: bool
    provider_deleted: bool
    row_version: int

    def validate_for(self, provider: MailboxProvider) -> None:
        self.identity.validate_for(provider)
        if (
            type(self.metadata_hash) is not str
            or len(self.metadata_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.metadata_hash
            )
            or type(self.unread) is not bool
            or type(self.starred) is not bool
            or type(self.provider_deleted) is not bool
            or type(self.row_version) is not int
            or self.row_version < 1
        ):
            raise ValueError("invalid message projection")


@dataclass(frozen=True, slots=True)
class MessageMutation:
    kind: MessageMutationKind
    projection: MessageProjection
    outbox_event_type: OutboxEventType

    def validate_for(self, provider: MailboxProvider) -> None:
        self.projection.validate_for(provider)
        if (
            self.kind is MessageMutationKind.TOMBSTONE
            and (
                self.outbox_event_type is not OutboxEventType.MESSAGE_DELETED
                or self.projection.provider_deleted is not True
            )
        ):
            raise ValueError("invalid tombstone mutation")


@dataclass(frozen=True, slots=True)
class ProviderDeltaCommit:
    scope: MailboxScope
    scope_key: str
    expected_state_row_version: int
    expected_cursor_row_version: int | None
    expected_cursor_generation: int
    mutations: tuple[MessageMutation, ...]
    next_cursor: SyncCursor
    next_bootstrap_state: BootstrapState

    def __post_init__(self) -> None:
        if (
            type(self.scope_key) is not str
            or not self.scope_key
            or type(self.expected_state_row_version) is not int
            or self.expected_state_row_version < 1
            or (
                self.expected_cursor_row_version is not None
                and (
                    type(self.expected_cursor_row_version) is not int
                    or self.expected_cursor_row_version < 1
                )
            )
            or type(self.expected_cursor_generation) is not int
            or self.expected_cursor_generation < 1
            or self.next_cursor.scope_key != self.scope_key
            or self.next_cursor.provider is not self.scope.provider
            or self.next_cursor.cursor_generation
            != self.expected_cursor_generation
        ):
            raise ValueError("invalid provider delta commit")

        for mutation in self.mutations:
            mutation.validate_for(self.scope.provider)


@dataclass(frozen=True, slots=True)
class CachedBody:
    message_id: str
    body_text: str | None
    body_html: str | None
    content_hash: str
    body_version: int
    row_version: int


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: str
    scope: MailboxScope
    message_id: str
    message_row_version: int
    event_type: OutboxEventType
    attempt_count: int


class MailboxRepository(Protocol):
    """Durable repository boundary.

    commit_provider_delta MUST use one PostgreSQL transaction. It must compare
    expected state/cursor versions and generations, apply every message
    mutation, insert the idempotent outbox rows, and advance the cursor before
    commit. A conflict or exception must publish none of those changes.
    """

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

    def read_cached_body(
        self,
        scope: MailboxScope,
        message_id: str,
    ) -> CachedBody | None:
        ...

    def commit_provider_delta(
        self,
        commit: ProviderDeltaCommit,
    ) -> DeltaCommitOutcome:
        ...

    def claim_outbox_batch(
        self,
        *,
        limit: int,
        now_millis: int,
    ) -> Sequence[OutboxEvent]:
        ...

    def mark_outbox_processed(
        self,
        event_id: str,
        *,
        processed_at_millis: int,
    ) -> bool:
        ...

    def mark_outbox_retry(
        self,
        event_id: str,
        *,
        next_attempt_at_millis: int,
        safe_error_code: str,
    ) -> bool:
        ...


__all__ = (
    "BackfillState",
    "BodyState",
    "BootstrapState",
    "CachedBody",
    "DeltaCommitOutcome",
    "MailboxProvider",
    "MailboxRepository",
    "MailboxScope",
    "MessageIdentity",
    "MessageMutation",
    "MessageMutationKind",
    "MessageProjection",
    "OutboxEvent",
    "OutboxEventType",
    "ProviderDeltaCommit",
    "SyncCursor",
)
